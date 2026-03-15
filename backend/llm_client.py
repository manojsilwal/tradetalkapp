"""
LLM Client — supports two backends, selected by environment variables:

  1. Ollama (primary)  — set OLLAMA_BASE_URL to use a local/tunnelled model
     e.g. OLLAMA_BASE_URL=https://learning-mills-sake-times.trycloudflare.com
          OLLAMA_MODEL=qwen3.5:9b
     Uses Ollama's /api/chat endpoint (native REST, no extra packages).

  2. Google Gemini (fallback) — set GEMINI_API_KEY
     Uses the google-genai SDK (google.genai).

  3. Rule-based templates — if neither is configured.

Each agent role has a locked finance-domain system prompt so agents behave as
investment specialists rather than generic assistants.
"""
import os
import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Env config ────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
_raw_model      = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODEL    = _raw_model if _raw_model.startswith("models/") else f"models/{_raw_model}"

# ── Finance-domain system prompts locked per agent role ──────────────────────
AGENT_SYSTEM_PROMPTS = {
    "bull": (
        "You are an aggressive growth investor and bull case analyst on a Wall Street panel. "
        "Your job is to identify the strongest bullish catalysts for a given stock using the "
        "data and historical context provided. Cite specific numbers. Be optimistic but grounded in data. "
        "ONLY discuss investment and financial topics. "
        "Respond ONLY with valid JSON: {\"headline\": \"one bold sentence\", "
        "\"key_points\": [\"point1\", \"point2\", \"point3\"], \"confidence\": 0.0-1.0}"
    ),
    "bear": (
        "You are a risk-averse fund manager and bear case analyst on a Wall Street panel. "
        "Your job is to identify the most serious risks and downside scenarios for a given stock "
        "using the data and historical context provided. Be cautious and precise. "
        "ONLY discuss investment and financial topics. "
        "Respond ONLY with valid JSON: {\"headline\": \"one bold sentence\", "
        "\"key_points\": [\"point1\", \"point2\", \"point3\"], \"confidence\": 0.0-1.0}"
    ),
    "macro": (
        "You are a senior macroeconomist on a Wall Street panel. "
        "Evaluate how the current macro environment — interest rates, inflation, credit stress, "
        "market regime — affects the investment case for a stock. "
        "ONLY discuss investment and financial topics. "
        "Respond ONLY with valid JSON: {\"headline\": \"one bold sentence\", "
        "\"key_points\": [\"point1\", \"point2\", \"point3\"], \"confidence\": 0.0-1.0}"
    ),
    "value": (
        "You are a Warren Buffett and Charlie Munger-inspired value investor on a Wall Street panel. "
        "Evaluate the intrinsic value, ROIC, free cash flow, and balance sheet quality of a stock. "
        "Think in decades, not quarters. ONLY discuss investment and financial topics. "
        "Respond ONLY with valid JSON: {\"headline\": \"one bold sentence\", "
        "\"key_points\": [\"point1\", \"point2\", \"point3\"], \"confidence\": 0.0-1.0}"
    ),
    "momentum": (
        "You are a quantitative momentum trader and technical analyst on a Wall Street panel. "
        "Evaluate price momentum, 52-week positioning, volume trends, and market psychology. "
        "Think in weeks and months. ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: {\"headline\": \"one bold sentence\", "
        "\"key_points\": [\"point1\", \"point2\", \"point3\"], \"confidence\": 0.0-1.0}"
    ),
    "moderator": (
        "You are a senior investment committee chair synthesising a structured debate between 5 "
        "specialist analysts. Given their arguments, produce a final investment verdict. "
        "Be decisive, balanced, and cite the weight of evidence. ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: {\"verdict\": \"STRONG BUY|BUY|NEUTRAL|SELL|STRONG SELL\", "
        "\"summary\": \"2-3 sentence plain-English explanation\"}"
    ),
    "strategy_parser": (
        "You are a quantitative analyst converting plain-English investing strategies into structured "
        "JSON rules for a backtesting engine. Extract filters, holding period, and universe. "
        "Be precise about metric names and threshold values. ONLY discuss investment topics. "
        "Respond ONLY with valid JSON matching the schema provided."
    ),
    "backtest_explainer": (
        "You are a quantitative finance researcher explaining backtesting results to investors. "
        "Given strategy rules and performance statistics, explain in plain English why the strategy "
        "performed the way it did. Reference specific metrics. Be educational and honest about risks. "
        "ONLY discuss investment and financial topics. "
        "Respond ONLY with valid JSON: {\"explanation\": \"2-3 paragraph explanation\"}"
    ),
}

# ── Rule-based fallback templates ─────────────────────────────────────────────
FALLBACK_TEMPLATES = {
    "bull":     {"headline": "Bullish signals detected in available market data.", "key_points": ["Short interest and squeeze potential identified.", "Positive revenue growth trend supports upside thesis.", "Sentiment indicators lean constructive."], "confidence": 0.55},
    "bear":     {"headline": "Risk factors warrant careful consideration before entry.", "key_points": ["Macro stress indicators are elevated.", "Debt-to-cash ratio requires monitoring.", "Valuation multiples leave limited margin of safety."], "confidence": 0.55},
    "macro":    {"headline": "Macro environment presents mixed signals for the sector.", "key_points": ["VIX and credit stress index are key watch metrics.", "Interest rate trajectory affects sector rotation.", "Market regime classification shapes risk appetite."], "confidence": 0.5},
    "value":    {"headline": "Fundamental quality is the primary long-term determinant.", "key_points": ["ROIC and ROE signal capital allocation efficiency.", "Free cash flow yield relative to price matters most.", "Balance sheet strength provides downside protection."], "confidence": 0.5},
    "momentum": {"headline": "Price action provides directional context for timing.", "key_points": ["52-week high/low positioning indicates trend strength.", "Recent price returns signal momentum continuation or reversal.", "Volume confirms or diverges from price moves."], "confidence": 0.5},
    "moderator": {"verdict": "NEUTRAL", "summary": "The panel presents mixed signals. A high-conviction directional call requires more data."},
}


class LLMClient:
    """
    Async LLM wrapper supporting Ollama (primary) and Gemini (fallback).
    Backend selected at startup based on environment variables.
    Uses asyncio.to_thread for all blocking HTTP calls.
    """

    def __init__(self):
        self._backend = "fallback"
        self._gemini_client = None
        # Ollama processes one request at a time — semaphore prevents pileup
        self._ollama_sem = asyncio.Semaphore(1)

        if OLLAMA_BASE_URL:
            self._backend = "ollama"
            logger.info(f"[LLMClient] Backend: Ollama — {OLLAMA_BASE_URL} | model: {OLLAMA_MODEL}")
        elif GEMINI_API_KEY:
            try:
                from google import genai
                self._gemini_client = genai.Client(api_key=GEMINI_API_KEY)
                self._backend = "gemini"
                logger.info(f"[LLMClient] Backend: Gemini — model: {GEMINI_MODEL}")
            except Exception as e:
                logger.warning(f"[LLMClient] Gemini init failed: {e}. Using fallback.")
        else:
            logger.warning("[LLMClient] No LLM configured (set OLLAMA_BASE_URL or GEMINI_API_KEY). Using rule-based fallback.")

    # ── Ollama ──────────────────────────────────────────────────────────────

    def _ollama_generate(self, role: str, prompt: str) -> dict:
        """Call Ollama /api/chat synchronously — run in a thread.
        Uses /no_think to disable Qwen3 chain-of-thought (faster, avoids token waste).
        """
        import requests as req
        system = AGENT_SYSTEM_PROMPTS.get(role, "You are a finance analyst. Respond only in valid JSON.")
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                # /no_think disables Qwen3's thinking mode — returns JSON directly
                {"role": "user", "content": f"/no_think\n\n{prompt}"},
            ],
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 1500,  # enough for JSON without thinking tokens
            },
            "think": False,          # Ollama >=0.7.1 flag to disable thinking
        }
        try:
            resp = req.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=300,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            msg = resp.json()["message"]
            # Qwen3 puts chain-of-thought in 'thinking', actual response in 'content'
            content = (msg.get("content") or "").strip()
            if not content:
                # thinking mode was active and ate all tokens — try thinking field
                content = (msg.get("thinking") or "").strip()
                logger.warning(f"[LLMClient] Ollama role={role}: content empty, falling back to thinking field")
            if not content:
                logger.warning(f"[LLMClient] Ollama role={role}: both content and thinking empty")
                return FALLBACK_TEMPLATES.get(role, {})
            return self._parse_json_response(content, role)
        except Exception as e:
            logger.warning(f"[LLMClient] Ollama call failed for role={role}: {e}")
            return FALLBACK_TEMPLATES.get(role, {})

    # ── Gemini ──────────────────────────────────────────────────────────────

    def _gemini_generate(self, role: str, prompt: str) -> dict:
        """Call Gemini synchronously — run in a thread."""
        system = AGENT_SYSTEM_PROMPTS.get(role, "You are a finance analyst.")
        full_prompt = f"{system}\n\n{prompt}"
        try:
            response = self._gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
            )
            content = response.text.strip()
            return self._parse_json_response(content, role)
        except Exception as e:
            logger.warning(f"[LLMClient] Gemini call failed for role={role}: {e}")
            return FALLBACK_TEMPLATES.get(role, {})

    # ── Shared ──────────────────────────────────────────────────────────────

    def _parse_json_response(self, content: str, role: str) -> dict:
        """Strip markdown fences and parse JSON. Return fallback on failure."""
        # Remove <think>...</think> blocks (Qwen3 chain-of-thought)
        import re
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Strip markdown code fences
        if "```" in content:
            parts = content.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    return json.loads(part)
                except Exception:
                    continue
        try:
            return json.loads(content)
        except Exception:
            # Try extracting first JSON object from the text
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
        logger.warning(f"[LLMClient] JSON parse failed for role={role}. Raw: {content[:200]}")
        return FALLBACK_TEMPLATES.get(role, {})

    async def generate(self, role: str, prompt: str) -> dict:
        """Async entry point — dispatches to the configured backend in a thread.
        Ollama calls are serialised via semaphore (one at a time) since Ollama
        queues requests internally anyway — concurrent calls just cause timeouts."""
        if self._backend == "ollama":
            async with self._ollama_sem:
                return await asyncio.to_thread(self._ollama_generate, role, prompt)
        if self._backend == "gemini":
            return await asyncio.to_thread(self._gemini_generate, role, prompt)
        return FALLBACK_TEMPLATES.get(role, {})

    async def generate_argument(self, role: str, ticker: str, live_data: dict, historical_context: str) -> dict:
        data_str = json.dumps(live_data, indent=2, default=str)
        prompt = (
            f"Ticker being debated: {ticker.upper()}\n\n"
            f"Live market data:\n{data_str}\n\n"
            f"Historical context from knowledge base:\n{historical_context}\n\n"
            f"Provide your {role} investment perspective on {ticker.upper()}."
        )
        return await self.generate(role, prompt)

    async def generate_moderator_verdict(self, ticker: str, arguments: list, historical_context: str) -> dict:
        args_str = "\n".join(
            f"[{a['agent_role']}] Stance: {a['stance']} — {a['headline']}\n"
            + "\n".join(f"  • {p}" for p in a["key_points"])
            for a in arguments
        )
        prompt = (
            f"Ticker: {ticker.upper()}\n\n"
            f"The 5 analyst arguments:\n{args_str}\n\n"
            f"Historical debate context:\n{historical_context}\n\n"
            f"Synthesise these into a final investment verdict."
        )
        return await self.generate("moderator", prompt)

    async def generate_strategy_rules(self, strategy_text: str, historical_context: str) -> dict:
        prompt = (
            f"Convert this investing strategy into structured JSON rules:\n\n"
            f"Strategy: {strategy_text}\n\n"
            f"Similar strategies from knowledge base:\n{historical_context}\n\n"
            f"Return JSON with keys: name (string), filters (list of objects with metric/op/value), "
            f"holding_period_months (int), rebalance_months (int), strategy_type (fundamental|momentum|mixed), "
            f"universe_hint (string describing what stocks to screen)."
        )
        return await self.generate("strategy_parser", prompt)

    async def generate_backtest_explanation(self, strategy_name: str, stats: dict, historical_context: str) -> str:
        stats_str = json.dumps(stats, indent=2, default=str)
        prompt = (
            f"Strategy: {strategy_name}\n\n"
            f"Backtest performance statistics:\n{stats_str}\n\n"
            f"Historical backtest context:\n{historical_context}\n\n"
            f"Explain in 2-3 paragraphs why this strategy performed the way it did. "
            f"Be educational, honest about risks, and reference specific statistics."
        )
        result = await self.generate("backtest_explainer", prompt)
        if isinstance(result, dict):
            return result.get("explanation", result.get("summary", str(result)))
        return str(result)

    @property
    def backend(self) -> str:
        return self._backend


# Module-level singleton
_llm_client: Optional[LLMClient] = None

def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
