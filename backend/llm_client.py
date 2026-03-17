"""
LLM Client — supports OpenRouter Nemotron and a rule-based fallback.

  1. OpenRouter (primary) — set OPENROUTER_API_KEY
     Optional:
       OPENROUTER_MODEL (default: nvidia/nemotron-3-super-120b-a12b:free)
       OPENROUTER_BASE_URL (default: https://openrouter.ai/api/v1)
       OPENROUTER_HTTP_REFERER
       OPENROUTER_X_TITLE

  2. Rule-based templates — if OPENROUTER_API_KEY is not configured.

Each agent role has a locked finance-domain system prompt so agents behave as
investment specialists rather than generic assistants.
"""
import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Env config ────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
OPENROUTER_HTTP_REFERER = os.environ.get("OPENROUTER_HTTP_REFERER", "")
OPENROUTER_X_TITLE = os.environ.get("OPENROUTER_X_TITLE", "TradeTalk App")
LLM_MAX_CONCURRENCY = max(1, int(os.environ.get("LLM_MAX_CONCURRENCY", "2")))
LLM_MAX_TOKENS = max(256, int(os.environ.get("LLM_MAX_TOKENS", "1500")))
RAG_TOP_K_DEFAULT = max(1, int(os.environ.get("RAG_TOP_K", "5")))

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
    Async LLM wrapper supporting OpenRouter Nemotron (primary).
    Backend selected at startup based on environment variables.
    Uses asyncio.to_thread for all blocking HTTP calls.
    """

    def __init__(self):
        self._backend = "fallback"
        self._openrouter_client = None
        # Guard concurrent upstream calls to avoid rate-limit bursts.
        self._openrouter_sem = asyncio.Semaphore(LLM_MAX_CONCURRENCY)

        if OPENROUTER_API_KEY:
            try:
                from openai import OpenAI

                headers = {}
                if OPENROUTER_HTTP_REFERER:
                    headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
                if OPENROUTER_X_TITLE:
                    headers["X-Title"] = OPENROUTER_X_TITLE

                self._openrouter_client = OpenAI(
                    base_url=OPENROUTER_BASE_URL,
                    api_key=OPENROUTER_API_KEY,
                    default_headers=headers,
                )
                self._backend = "openrouter"
                logger.info(f"[LLMClient] Backend: OpenRouter — model: {OPENROUTER_MODEL}")
            except Exception as e:
                logger.warning(f"[LLMClient] OpenRouter init failed: {e}. Using fallback.")
        else:
            logger.warning("[LLMClient] No LLM configured (set OPENROUTER_API_KEY). Using rule-based fallback.")

    # ── OpenRouter Nemotron ────────────────────────────────────────────────

    def _openrouter_generate(self, role: str, prompt: str) -> dict:
        """Call OpenRouter Chat Completions synchronously — run in a thread."""
        system = AGENT_SYSTEM_PROMPTS.get(role, "You are a finance analyst. Respond only in valid JSON.")
        try:
            completion = self._openrouter_client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=LLM_MAX_TOKENS,
            )
            content = (completion.choices[0].message.content or "").strip()
            if not content:
                logger.warning(f"[LLMClient] OpenRouter role={role}: empty response content")
                return FALLBACK_TEMPLATES.get(role, {})
            return self._parse_json_response(content, role)
        except Exception as e:
            logger.warning(f"[LLMClient] OpenRouter call failed for role={role}: {e}")
            return FALLBACK_TEMPLATES.get(role, {})

    # ── Shared ──────────────────────────────────────────────────────────────

    def _parse_json_response(self, content: str, role: str) -> dict:
        """Strip markdown fences and parse JSON. Return fallback on failure."""
        # Remove <think>...</think> blocks if the model includes reasoning tags.
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
        """Async entry point — dispatches to the configured backend in a thread."""
        if self._backend == "openrouter":
            async with self._openrouter_sem:
                return await asyncio.to_thread(self._openrouter_generate, role, prompt)
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
            f"Convert this plain-English investing strategy into structured JSON rules.\n\n"
            f"Strategy: {strategy_text}\n\n"
            f"Similar strategies from knowledge base:\n{historical_context}\n\n"
            f"Return JSON with these keys:\n"
            f"  name (string): short descriptive strategy name\n"
            f"  filters (list): BUY entry conditions — each is {{metric, op, value}}\n"
            f"  sell_filters (list): SELL exit conditions — each is {{metric, op, value}}. "
            f"    IMPORTANT: if the strategy has explicit sell conditions (e.g. 'sell when PE > 35'), "
            f"    put them here. Leave empty [] if no explicit sell trigger (periodic rebalance).\n"
            f"  holding_period_months (int): how long to hold (12 if unspecified)\n"
            f"  rebalance_months (int): 1 if sell_filters present (monthly check), else 12\n"
            f"  strategy_type (string): 'fundamental', 'momentum', or 'mixed'\n"
            f"  universe_hint (string): e.g. 'mag7', 'S&P 500', 'dividend stocks'\n\n"
            f"Supported metrics: forward_pe, pe_ratio, revenue_growth_yoy, net_income_growth_yoy, "
            f"debt_to_equity, pb_ratio, roe, roa, dividend_yield, gross_margins, "
            f"price_return_1m, price_return_3m, price_return_6m, price_return_1y, "
            f"above_ma_200, above_ma_50\n\n"
            f"Note: forward_pe and pe_ratio are computed from trailing-12-month EPS history."
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
        try:
            result = await self.generate("backtest_explainer", prompt)
            if isinstance(result, dict):
                return result.get("explanation", result.get("summary", str(result)))
            return str(result)
        except Exception as e:
            # LLM unavailable (e.g. tunnel down) — return a statistical summary instead
            logger.warning(f"[LLMClient] generate_backtest_explanation unavailable: {e}")
            cagr = stats.get("cagr", 0)
            ret  = stats.get("total_return_pct", 0)
            spy  = stats.get("benchmark_cagr", 0)
            return (
                f"{strategy_name} achieved a {cagr:+.1f}% CAGR over the backtest period, "
                f"returning {ret:+.1f}% total vs SPY's {spy:+.1f}% CAGR. "
                f"AI explanation unavailable — LLM backend unreachable."
            )

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
