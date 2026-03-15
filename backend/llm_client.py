"""
LLM Client — wraps Google Gemini for all AI-powered agent reasoning.
Each agent role has a locked finance-domain system prompt so agents behave as
investment specialists rather than generic assistants.
Falls back to rule-based template strings if GEMINI_API_KEY is not set.
"""
import os
import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# The google.genai SDK requires the "models/" prefix
_raw_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODEL = _raw_model if _raw_model.startswith("models/") else f"models/{_raw_model}"

# ── Finance-domain system prompts locked per agent role ──────────────────────
AGENT_SYSTEM_PROMPTS = {
    "bull": (
        "You are an aggressive growth investor and bull case analyst on a Wall Street panel. "
        "Your job is to identify the strongest bullish catalysts for a given stock using the "
        "data and historical context provided. You cite specific numbers. You are optimistic "
        "but grounded in data. You ONLY discuss investment and financial topics. "
        "Respond in JSON with keys: headline (one bold sentence), key_points (list of 3-5 strings), confidence (0.0-1.0)."
    ),
    "bear": (
        "You are a risk-averse fund manager and bear case analyst on a Wall Street panel. "
        "Your job is to identify the most serious risks and downside scenarios for a given stock "
        "using the data and historical context provided. You are cautious and precise. "
        "You ONLY discuss investment and financial topics. "
        "Respond in JSON with keys: headline (one bold sentence), key_points (list of 3-5 strings), confidence (0.0-1.0)."
    ),
    "macro": (
        "You are a senior macroeconomist on a Wall Street panel. "
        "Your job is to evaluate how the current macroeconomic environment — interest rates, "
        "inflation, credit stress, market regime — affects the investment case for a stock. "
        "You ONLY discuss investment and financial topics. "
        "Respond in JSON with keys: headline (one bold sentence), key_points (list of 3-5 strings), confidence (0.0-1.0)."
    ),
    "value": (
        "You are a Warren Buffett and Charlie Munger-inspired value investor on a Wall Street panel. "
        "Your job is to evaluate the intrinsic value, ROIC, free cash flow, and balance sheet quality "
        "of a stock. You think in decades, not quarters. You ONLY discuss investment and financial topics. "
        "Respond in JSON with keys: headline (one bold sentence), key_points (list of 3-5 strings), confidence (0.0-1.0)."
    ),
    "momentum": (
        "You are a quantitative momentum trader and technical analyst on a Wall Street panel. "
        "Your job is to evaluate price momentum, 52-week positioning, volume trends, and market "
        "psychology for a stock. You think in weeks and months. You ONLY discuss investment topics. "
        "Respond in JSON with keys: headline (one bold sentence), key_points (list of 3-5 strings), confidence (0.0-1.0)."
    ),
    "moderator": (
        "You are a senior investment committee chair synthesizing a structured debate between 5 "
        "specialist analysts. Given their arguments, produce a final investment verdict. "
        "Be decisive, balanced, and cite the weight of evidence. You ONLY discuss investment topics. "
        "Respond in JSON with keys: verdict (one of: STRONG BUY, BUY, NEUTRAL, SELL, STRONG SELL), "
        "summary (2-3 sentence plain-English explanation of the verdict)."
    ),
    "strategy_parser": (
        "You are a quantitative analyst converting plain-English investing strategies into structured "
        "JSON rules for a backtesting engine. Extract filters, holding period, and universe from the "
        "user's description. Be precise about metric names and threshold values. "
        "You ONLY discuss investment and financial topics."
    ),
    "backtest_explainer": (
        "You are a quantitative finance researcher explaining backtesting results to investors. "
        "Given strategy rules and performance statistics, explain in plain English why the strategy "
        "performed the way it did. Reference specific metrics. Be educational and honest about risks. "
        "You ONLY discuss investment and financial topics."
    ),
}

# ── Fallback templates (used when no API key is set) ─────────────────────────
FALLBACK_TEMPLATES = {
    "bull":      {"headline": "Bullish signals detected in the data.", "key_points": ["Short interest elevated — squeeze potential exists.", "Positive social sentiment observed.", "Revenue growth metrics support upside thesis."], "confidence": 0.6},
    "bear":      {"headline": "Significant risk factors warrant caution.", "key_points": ["Macro stress indicators are elevated.", "Debt levels relative to cash are concerning.", "Valuation multiples leave little margin of safety."], "confidence": 0.6},
    "macro":     {"headline": "Macro environment presents mixed signals.", "key_points": ["VIX and credit stress index are key watch metrics.", "Interest rate trajectory affects sector rotation.", "Market regime classification shapes risk appetite."], "confidence": 0.5},
    "value":     {"headline": "Fundamental quality determines long-term outcome.", "key_points": ["ROIC and ROE signal capital efficiency.", "Free cash flow yield relative to price matters most.", "Balance sheet strength provides downside protection."], "confidence": 0.55},
    "momentum":  {"headline": "Price action provides directional clues.", "key_points": ["52-week high/low position indicates trend strength.", "Recent price returns signal momentum continuation.", "Volume confirms or diverges from price moves."], "confidence": 0.5},
    "moderator": {"verdict": "NEUTRAL", "summary": "Agents present mixed signals. Further data required for a high-conviction call."},
}


class LLMClient:
    """
    Async wrapper around Google Gemini. One instance shared across all agents.
    Uses asyncio.to_thread since the google-generativeai SDK is synchronous.
    """

    def __init__(self):
        self._client = None
        self._available = False
        if GEMINI_API_KEY:
            try:
                from google import genai
                self._client = genai.Client(api_key=GEMINI_API_KEY)
                self._available = True
                logger.info(f"[LLMClient] Gemini initialised — model: {GEMINI_MODEL}")
            except Exception as e:
                logger.warning(f"[LLMClient] Gemini init failed: {e}. Using fallback.")
        else:
            logger.warning("[LLMClient] GEMINI_API_KEY not set. Using rule-based fallback.")

    def _sync_generate(self, role: str, prompt: str) -> dict:
        """Synchronous Gemini call — run inside a thread."""
        system_prompt = AGENT_SYSTEM_PROMPTS.get(role, "You are a finance analyst.")
        full_prompt = f"{system_prompt}\n\n{prompt}"
        try:
            response = self._client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
            )
            text = response.text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception as e:
            logger.warning(f"[LLMClient] Gemini call failed for role={role}: {e}")
            return FALLBACK_TEMPLATES.get(role, {})

    async def generate(self, role: str, prompt: str) -> dict:
        """Async entry point — dispatches to thread pool."""
        if not self._available or not self._client:
            return FALLBACK_TEMPLATES.get(role, {})
        return await asyncio.to_thread(self._sync_generate, role, prompt)

    async def generate_argument(
        self,
        role: str,
        ticker: str,
        live_data: dict,
        historical_context: str,
    ) -> dict:
        """Generate a debate argument for the given agent role."""
        data_str = json.dumps(live_data, indent=2, default=str)
        prompt = (
            f"Ticker being debated: {ticker.upper()}\n\n"
            f"Live market data:\n{data_str}\n\n"
            f"Historical context from knowledge base (most relevant past analyses):\n{historical_context}\n\n"
            f"Provide your {role} investment perspective on {ticker.upper()}."
        )
        return await self.generate(role, prompt)

    async def generate_moderator_verdict(
        self,
        ticker: str,
        arguments: list,
        historical_context: str,
    ) -> dict:
        """Synthesise 5 agent arguments into a final verdict."""
        args_str = "\n".join(
            f"[{a['agent_role']}] Stance: {a['stance']} — {a['headline']}\n"
            + "\n".join(f"  • {p}" for p in a["key_points"])
            for a in arguments
        )
        prompt = (
            f"Ticker: {ticker.upper()}\n\n"
            f"The 5 analyst arguments:\n{args_str}\n\n"
            f"Historical debate context:\n{historical_context}\n\n"
            f"Synthesise these perspectives into a final investment verdict."
        )
        return await self.generate("moderator", prompt)

    async def generate_strategy_rules(self, strategy_text: str, historical_context: str) -> dict:
        """Parse plain-English strategy into structured rules."""
        prompt = (
            f"Convert this investing strategy into structured JSON rules:\n\n"
            f"Strategy: {strategy_text}\n\n"
            f"Similar strategies from knowledge base:\n{historical_context}\n\n"
            f"Return JSON with keys: name (string), filters (list of objects with metric/op/value), "
            f"holding_period_months (int), rebalance_months (int), strategy_type (fundamental|momentum|mixed), "
            f"universe_hint (string describing what stocks to screen)."
        )
        return await self.generate("strategy_parser", prompt)

    async def generate_backtest_explanation(
        self,
        strategy_name: str,
        stats: dict,
        historical_context: str,
    ) -> str:
        """Explain backtest results in plain English."""
        stats_str = json.dumps(stats, indent=2, default=str)
        prompt = (
            f"Strategy: {strategy_name}\n\n"
            f"Backtest performance statistics:\n{stats_str}\n\n"
            f"Historical backtest context from knowledge base:\n{historical_context}\n\n"
            f"Explain in 2-3 paragraphs why this strategy performed the way it did. "
            f"Be educational, honest about risks, and reference specific statistics."
        )
        result = await self.generate("backtest_explainer", prompt)
        if isinstance(result, dict):
            return result.get("explanation", result.get("summary", str(result)))
        return str(result)


# Module-level singleton
_llm_client: Optional[LLMClient] = None

def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
