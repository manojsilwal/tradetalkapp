"""
LLM Client — supports OpenRouter Nemotron and a rule-based fallback.

  1. OpenRouter (primary) — set OPENROUTER_API_KEY
     Inference goes DIRECT to OpenRouter, never proxied through HF Space.
     Optional:
       OPENROUTER_MODEL (default: nvidia/nemotron-3-super-120b-a12b:free)
       OPENROUTER_BASE_URL (default: https://openrouter.ai/api/v1)
       OPENROUTER_HTTP_REFERER
       OPENROUTER_X_TITLE

  2. Rule-based templates — if OPENROUTER_API_KEY is not configured.

Agent policy guardrails (defense-in-depth) are enforced via
agent_policy_guardrails when enabled.  These are in-process checks and
do NOT replace OS/container-level isolation.

Each agent role has a locked finance-domain system prompt so agents behave as
investment specialists rather than generic assistants.
"""
import asyncio
import json
import logging
import os
from typing import Optional
from .agent_policy_guardrails import (
    guard_host,
    is_enabled as policy_guardrails_enabled,
    redact_secrets_in_text,
    workload_scope,
)

logger = logging.getLogger(__name__)

# ── Env config ────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
OPENROUTER_MODEL_LIGHT = os.environ.get("OPENROUTER_MODEL_LIGHT", OPENROUTER_MODEL)
OPENROUTER_HTTP_REFERER = os.environ.get("OPENROUTER_HTTP_REFERER", "")
OPENROUTER_X_TITLE = os.environ.get("OPENROUTER_X_TITLE", "TradeTalk App")
GUARDRAILS_ENABLE = os.environ.get("GUARDRAILS_ENABLE", "1").strip() != "0"
LLM_MAX_CONCURRENCY = max(1, int(os.environ.get("LLM_MAX_CONCURRENCY", "2")))
LLM_MAX_TOKENS = max(256, int(os.environ.get("LLM_MAX_TOKENS", "1500")))
RAG_TOP_K_DEFAULT = max(1, int(os.environ.get("RAG_TOP_K", "5")))

# ── Role-to-model tier mapping ────────────────────────────────────────────────
# High-reasoning roles need frontier-class models; lightweight roles can use
# cheaper/faster models when OPENROUTER_MODEL_LIGHT is set to a different model.
MODEL_TIER = {
    "bull":               "heavy",
    "bear":               "heavy",
    "macro":              "heavy",
    "value":              "heavy",
    "momentum":           "heavy",
    "moderator":          "heavy",
    "strategy_parser":    "heavy",
    "backtest_explainer": "heavy",
    "gold_advisor":       "heavy",
    "swarm_synthesizer":  "light",
    "swarm_analyst":      "light",
    "swarm_reflection_writer": "light",
    "video_scene_director": "light",
    "rag_narrative_polish": "light",
}

def _model_for_role(role: str) -> str:
    tier = MODEL_TIER.get(role, "heavy")
    return OPENROUTER_MODEL_LIGHT if tier == "light" else OPENROUTER_MODEL

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
    "swarm_synthesizer": (
        "You are a senior investment committee member resolving disagreements among factor analysts. "
        "Given factor results that disagree, explain WHY they disagree in 2 sentences and produce a "
        "confidence-weighted verdict. ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: {\"consensus_rationale\": \"2-sentence explanation\", "
        "\"verdict\": \"STRONG BUY|BUY|NEUTRAL|SELL|STRONG SELL\", \"confidence\": 0.0-1.0}"
    ),
    "swarm_analyst": (
        "You are a quantitative analyst reviewing ambiguous market data for a single factor. "
        "Given the raw data and up to 2 prior lessons from past analyses, determine a trading signal. "
        "Reason step by step about why the data is ambiguous and what the prior lessons suggest. "
        "ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: {\"signal\": -1|0|1, \"rationale\": \"reasoning\", \"confidence\": 0.0-1.0}"
    ),
    "swarm_reflection_writer": (
        "You are a post-trade analyst writing a structured lesson learned from a swarm prediction. "
        "Given the original signal, confidence, and next-day price change, explain why the swarm "
        "was right or wrong and what to remember for future analyses. "
        "ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: {\"lesson\": \"1-2 sentence lesson\"}"
    ),
    "video_scene_director": (
        "You are a visual director creating short educational finance scene plans. "
        "Return ONLY valid JSON in this shape: "
        "{\"scenes\":[{\"scene\":1,\"visual_prompt\":\"...\",\"caption\":\"...\",\"duration\":8}]}. "
        "No markdown fences."
    ),
    "gold_advisor": (
        "You are a precious-metals allocator advisor for LONG-TERM investors (not day traders). "
        "You receive a JSON snapshot: macro (VIX, 10Y TIPS real yield, nominal 10Y, DXY, gold futures), "
        "pre-computed daily technicals (RSI, MACD, Bollinger, ATR, pivots — do NOT recalculate), "
        "headline sentiment score, and calendar hints. "
        "Explain how real yields and the dollar typically relate to gold; be nuanced — correlation breaks. "
        "No buy/sell orders; frame as education and risk awareness. "
        "ONLY discuss investment and macro topics. "
        "Respond ONLY with valid JSON: {\"directional_bias\":\"constructive|neutral|caution\", "
        "\"summary\":\"3-5 sentences plain English\", "
        "\"key_drivers\":[\"bullet1\",\"bullet2\",\"bullet3\"], "
        "\"levels_to_watch\":\"reference pivot/ATR/MA levels from the data when useful\", "
        "\"risk_factors\":[\"bullet1\",\"bullet2\"], "
        "\"confidence_0_1\":0.0-1.0}"
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
    "swarm_synthesizer": {"consensus_rationale": "Factors are split — insufficient conviction for a directional call.", "verdict": "NEUTRAL", "confidence": 0.5},
    "swarm_analyst": {"signal": 0, "rationale": "Data falls in ambiguous range; defaulting to neutral.", "confidence": 0.5},
    "swarm_reflection_writer": {"lesson": "Insufficient data to derive a clear lesson."},
    "video_scene_director": {"scenes": []},
    "gold_advisor": {
        "directional_bias": "neutral",
        "summary": (
            "Gold is often sensitive to real interest rates and the dollar, but relationships vary. "
            "Review the attached TIPS yield, DXY, and VIX alongside technical structure before sizing exposure."
        ),
        "key_drivers": [
            "Real yields (TIPS) and USD strength are classic headwinds or tailwinds for bullion.",
            "Risk sentiment (VIX spikes) can drive short-term safe-haven demand.",
            "Position sizing should reflect volatility (ATR) and your time horizon.",
        ],
        "levels_to_watch": "Use classic pivots and recent MA levels from the technicals block as reference zones only.",
        "risk_factors": [
            "Policy surprises can move rates and gold faster than fundamentals suggest.",
            "Futures-based snapshots may not match physical gold or ETF execution prices.",
        ],
        "confidence_0_1": 0.45,
    },
}


class LLMClient:
    """
    Async LLM wrapper — sends inference DIRECT to OpenRouter.
    The HF Space is an agent runtime, NOT an inference proxy.
    Policy guardrails (in-process, defense-in-depth) are enforced when enabled.
    Uses asyncio.to_thread for all blocking HTTP calls.
    """

    def __init__(self):
        self._backend = "fallback"
        self._provider = "fallback"
        self._model = ""
        self._endpoint = ""
        self._client = None
        self._sem = asyncio.Semaphore(LLM_MAX_CONCURRENCY)
        self._init_client()

    def _init_client(self):
        try:
            from openai import OpenAI
        except Exception as e:
            logger.warning("[LLMClient] openai sdk unavailable: %s", redact_secrets_in_text(str(e)))
            return

        headers = {}
        if OPENROUTER_HTTP_REFERER:
            headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
        if OPENROUTER_X_TITLE:
            headers["X-Title"] = OPENROUTER_X_TITLE

        if OPENROUTER_API_KEY:
            try:
                if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                    guard_host("llm", OPENROUTER_BASE_URL)
                self._client = OpenAI(
                    base_url=OPENROUTER_BASE_URL,
                    api_key=OPENROUTER_API_KEY,
                    default_headers=headers,
                )
                self._provider = "openrouter"
                self._backend = "openrouter"
                self._model = OPENROUTER_MODEL
                self._endpoint = OPENROUTER_BASE_URL
                logger.info("[LLMClient] Backend: OpenRouter direct — model: %s", OPENROUTER_MODEL)
                return
            except Exception as e:
                logger.warning("[LLMClient] OpenRouter init failed: %s", redact_secrets_in_text(str(e)))

        logger.warning("[LLMClient] No OPENROUTER_API_KEY configured. Using rule-based fallback.")

    # ── Inference ──────────────────────────────────────────────────────────

    def _provider_generate(self, role: str, prompt: str) -> dict:
        """Call OpenRouter synchronously — run in a thread."""
        system = AGENT_SYSTEM_PROMPTS.get(role, "You are a finance analyst. Respond only in valid JSON.")
        model = _model_for_role(role)
        try:
            if self._client is None:
                return FALLBACK_TEMPLATES.get(role, {})
            if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                with workload_scope("llm", "llm_inference"):
                    completion = self._client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.3,
                        max_tokens=LLM_MAX_TOKENS,
                    )
            else:
                completion = self._client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=LLM_MAX_TOKENS,
                )
            content = (completion.choices[0].message.content or "").strip()
            if not content:
                logger.warning("[LLMClient] role=%s model=%s empty response", role, model)
                return FALLBACK_TEMPLATES.get(role, {})
            return self._parse_json_response(content, role)
        except Exception as e:
            logger.warning(
                "[LLMClient] call failed role=%s model=%s err=%s",
                role,
                model,
                redact_secrets_in_text(str(e)),
            )
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
        """Async entry point — dispatches to OpenRouter in a thread."""
        if self._provider == "openrouter":
            async with self._sem:
                return await asyncio.to_thread(self._provider_generate, role, prompt)
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

    async def generate_gold_briefing(self, context: dict) -> dict:
        """Investor gold snapshot → structured briefing (deterministic context, LLM synthesis)."""
        ctx = json.dumps(context, indent=2, default=str)
        if len(ctx) > 14_000:
            ctx = ctx[:14_000] + "\n…(truncated)"
        prompt = (
            "Here is today's deterministic Gold Advisor context (JSON). "
            "Synthesize an investor briefing. Do not recalculate indicators.\n\n"
            f"{ctx}"
        )
        return await self.generate("gold_advisor", prompt)

    async def generate_swarm_synthesis(self, ticker: str, factor_results: list[dict]) -> dict:
        """Resolve disagreements among swarm factor agents."""
        factors_str = "\n".join(
            f"- {f['factor_name']}: signal={f['trading_signal']}, confidence={f['confidence']:.2f}, "
            f"status={f['status']}, rationale={f['rationale'][:200]}"
            for f in factor_results
        )
        prompt = (
            f"Ticker: {ticker.upper()}\n\n"
            f"Factor results from the swarm:\n{factors_str}\n\n"
            f"The factors disagree. Explain why and produce a final verdict."
        )
        return await self.generate("swarm_synthesizer", prompt)

    async def generate_swarm_analyst_call(self, factor_name: str, ticker: str,
                                          raw_data: dict, prior_lessons: list[str]) -> dict:
        """LLM reasoning for ambiguous-zone swarm analyst steps."""
        data_str = json.dumps(raw_data, indent=2, default=str)
        lessons_str = "\n".join(f"  - {l}" for l in prior_lessons) if prior_lessons else "None available."
        prompt = (
            f"Factor: {factor_name}\nTicker: {ticker.upper()}\n\n"
            f"Raw data:\n{data_str}\n\n"
            f"Prior lessons:\n{lessons_str}\n\n"
            f"The data is in an ambiguous range. Reason step by step and determine a signal."
        )
        return await self.generate("swarm_analyst", prompt)

    async def generate_swarm_reflection(self, ticker: str, signal: int, verdict: str,
                                        confidence: float, price_change_pct: float,
                                        regime: str) -> dict:
        """Write a structured lesson from a swarm prediction vs actual outcome."""
        outcome = "correct" if (signal > 0 and price_change_pct > 0) or (signal <= 0 and price_change_pct <= 0) else "incorrect"
        prompt = (
            f"Ticker: {ticker.upper()}\n"
            f"Swarm signal: {signal} ({verdict}), confidence: {confidence:.2f}\n"
            f"Next-day price change: {price_change_pct:+.2f}%\n"
            f"Market regime: {regime}\n"
            f"Outcome: {outcome}\n\n"
            f"Write a 1-2 sentence lesson for future swarm analyses."
        )
        return await self.generate("swarm_reflection_writer", prompt)

    def _plain_text_generate_sync(self, system: str, user: str) -> str:
        """Single chat completion returning raw assistant text (no JSON parse)."""
        model = OPENROUTER_MODEL_LIGHT
        try:
            if self._client is None:
                return user
            if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                with workload_scope("llm", "llm_inference"):
                    completion = self._client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=0.2,
                        max_tokens=min(900, LLM_MAX_TOKENS),
                    )
            else:
                completion = self._client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.2,
                    max_tokens=min(900, LLM_MAX_TOKENS),
                )
            out = (completion.choices[0].message.content or "").strip()
            return out if len(out) > 40 else user
        except Exception as e:
            logger.warning(
                "[LLMClient] plain_text_generate failed: %s",
                redact_secrets_in_text(str(e)),
            )
            return user

    async def generate_rag_polish(self, context_label: str, draft: str) -> str:
        """
        Phase 5 — tighten data-lake summaries for embedding/RAG (Nemotron via OpenRouter).
        Falls back to draft when API key missing or on error.
        """
        if self._provider != "openrouter" or self._client is None:
            return draft
        system = (
            "You are a financial data editor. Rewrite notes into one dense factual paragraph "
            "for vector search (RAG). No buy/sell recommendations. Keep all numeric facts. "
            "Plain prose only — no JSON, no markdown headings, no bullet lists."
        )
        user = f"Label: {context_label}\n\nDRAFT:\n{draft[:12000]}\n\nOutput one improved paragraph:"
        async with self._sem:
            return await asyncio.to_thread(self._plain_text_generate_sync, system, user)

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return self._endpoint


# Module-level singleton
_llm_client: Optional[LLMClient] = None

def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
