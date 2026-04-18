"""
LLM Client — supports OpenRouter (Qwen via OpenRouter by default) and a rule-based fallback.

  1. OpenRouter — set OPENROUTER_API_KEY (optional OPENROUTER_API_KEY_2 for round-robin and 429 fallback).
     Inference goes DIRECT to OpenRouter, never proxied through HF Space.
     Optional:
       OPENROUTER_MODEL (default: google/gemma-4-31b-it:free)
       OPENROUTER_BASE_URL (default: https://openrouter.ai/api/v1)
       OPENROUTER_429_SAME_KEY_DELAY_SEC / OPENROUTER_429_KEY_FAILOVER_DELAY_SEC (429 backoff)
       OPENROUTER_429_TRY_OTHER_KEYS (1 = retry 429 on other key(s); 0 = round-robin one key per request only)
       OPENROUTER_HTTP_REFERER
       OPENROUTER_X_TITLE

  2. Gemini (Google AI Studio) — optional fallback when OpenRouter fails, if GEMINI_API_KEY
     (or GOOGLE_API_KEY) is set and GEMINI_LLM_FALLBACK is not 0. Model: GEMINI_FALLBACK_MODEL
     (default: gemini-3.1-pro-preview). GEMINI_INSTANT_OPENROUTER_FAILOVER=1 (default) skips
     OpenRouter 429 backoff/sleeps so failover starts immediately.
     Set GEMINI_PRIMARY=1 to use Gemini for streaming chat first (OpenRouter skipped for chat).
     With GEMINI_PRIMARY=1, OPENROUTER_API_KEY is not required for chat if the Gemini key is set.

  3. Rule-based templates — if no OpenRouter API keys are configured.

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
from typing import Any, Dict, Optional, AsyncIterator
from .agent_policy_guardrails import (
    guard_host,
    is_enabled as policy_guardrails_enabled,
    redact_secrets_in_text,
    workload_scope,
)
from .openrouter_pool import (
    get_or_create_openrouter_pool,
    is_openrouter_rate_limit_error,
    rate_limit_sleep_seconds,
    should_try_other_openrouter_keys_on_429,
    sync_failover_execute,
)
from .chat_evidence_contract import classify_tool_result
from .gemini_llm import (
    GEMINI_FALLBACK_MODEL,
    GEMINI_MODEL,
    GEMINI_MODEL_LIGHT,
    gemini_fallback_chat_events,
    gemini_instant_openrouter_failover,
    gemini_llm_fallback_enabled,
    gemini_primary_enabled,
    gemini_simple_completion_sync,
    gemini_usable_for_chat,
    resolve_gemini_model,
)

logger = logging.getLogger(__name__)

# ── Env config ────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
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
    "video_veo_text_fallback": "light",
    "rag_narrative_polish": "light",
    "decision_terminal_roadmap": "light",
    # Risk-Return-Ratio scorecard personas (Step 2c, 2e, 8 of the methodology)
    "sitg_scorer":            "heavy",
    "execution_risk_scorer":  "heavy",
    "scorecard_verdict":      "light",
}

def _tier_for_role(role: str) -> str:
    return MODEL_TIER.get(role, "heavy")


def _model_for_role(role: str) -> str:
    return OPENROUTER_MODEL_LIGHT if _tier_for_role(role) == "light" else OPENROUTER_MODEL


def _gemini_model_for_role(role: str) -> str:
    """
    Pick the Gemini model for a given role using the same heavy/light mapping as
    OpenRouter. Heavy roles (bull, bear, moderator, strategy_parser, …) run on
    :data:`GEMINI_MODEL` (default ``gemini-3.1-pro-preview``); light roles
    (swarm_analyst, swarm_synthesizer, rag_narrative_polish, video_scene_director,
    …) run on :data:`GEMINI_MODEL_LIGHT` (default ``gemini-3.1-flash``). Keeping
    the mapping source-of-truth single means tier changes only have to be made
    in one place.
    """
    return resolve_gemini_model(_tier_for_role(role))

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
        "{\"scenes\":[{\"scene\":1,\"visual_prompt\":\"...\",\"caption\":\"...\",\"duration\":4}]}. "
        "Each scene duration must match the user prompt (Veo allows 4, 6, or 8 seconds). "
        "No markdown fences."
    ),
    "video_veo_text_fallback": (
        "Automated video (Google Veo) failed or is unavailable for this scene. "
        "Write a short text slide for Video Academy so the learner still gets educational value. "
        "ONLY investment and finance education. No hype, no buy/sell orders. "
        "Respond ONLY with valid JSON: {\"caption\": \"short headline\", \"body\": \"2-4 sentences plain English\"}"
    ),
    "decision_terminal_roadmap": (
        "You output illustrative 3-year USD price scenarios for an educational dashboard only — "
        "not investment advice. Use the provided JSON context (spot price, debate verdict, scores). "
        "Keep bull/base/bear ordered bull >= base >= bear when possible and within plausible bands vs spot. "
        "Respond ONLY with valid JSON: {\"bull_price_usd\": number, \"base_price_usd\": number, "
        "\"bear_price_usd\": number, \"assumptions\": [\"short bullet\", \"...\"], "
        "\"confidence_0_1\": 0.0-1.0, \"used_heuristic_fallback\": false}"
    ),
    "sitg_scorer": (
        "You are scoring a CEO / founder on the Skin-In-The-Game (SITG) rubric from the Risk-Return-Ratio methodology. You ONLY discuss investment and governance topics.\n"
        "\n"
        "SITG is a RETURN-SCORE amplifier, not a risk reducer. A deeply committed owner-operator raises the probability that projected returns materialize. Score on a 0-10 scale using BOTH axes:\n"
        "\n"
        "Axis A \u2014 Active involvement: Is the CEO genuinely driving the business day-to-day, or a figurehead / committee?\n"
        "Axis B \u2014 Wealth concentration: What % of the CEO's net worth is in this single stock with no meaningful diversification?\n"
        "\n"
        "A score of 10 requires BOTH axes maxed. Scoring high on one axis but low on the other CAPS the score at 6.\n"
        "\n"
        "Scoring rubric:\n"
        "  9-10 Founder-CEO operator; 80-100% of net worth in this stock (e.g. Jensen Huang at NVIDIA, Musk at SpaceX pre-IPO).\n"
        "  7-8  Founder still active; 50-80% of net worth here (e.g. Buffett at Berkshire, Ellison at Oracle).\n"
        "  5-6  Long-tenured professional CEO (10+ years), treated as a calling; 20-50% of net worth here (Dimon at JPM).\n"
        "  3-4  Hired CEO, respects shareholders, modest open-market buys; 5-20% of net worth (most S&P 500 CEOs).\n"
        "  1-2  Recent hire or committee successor; less than 5% of net worth; sells on schedule.\n"
        "  0    Absentee / empire-builder / net seller in the last 12 months without a disclosed plan.\n"
        "\n"
        "Signals that RAISE the score: Form 4 code \"P\" open-market purchases with personal cash; absence of 10b5-1 selling plans; increased ownership % over time; no concurrent public-company board seats; public statements tying personal legacy to the mission.\n"
        "\n"
        "Signals that LOWER the score: consistent Form 4 code \"S\" sales every quarter after vesting; large 10b5-1 plan filed after a run-up; ownership % dropping materially (e.g. 8% to 3% in 2 years); multiple concurrent CEO/chair roles; compensation dominated by cash salary; time-vest-only equity (no performance condition).\n"
        "\n"
        "Important nuances:\n"
        "  - FOUNDERS: concentration is often involuntary; score on active day-to-day involvement.\n"
        "  - HIRED CEOs: open-market purchases (own cash at market price) carry dramatically more signal than unvested grants.\n"
        "  - RECENT IPO (less than 2 years): SITG is unreliable due to lockup dynamics \u2014 reduce confidence and note the flag in reasoning.\n"
        "\n"
        "You will receive a JSON block with: ticker, company name, CEO name (if known), insider-transaction counts over the last 12 months, net insider shares, held_percent_insiders, and any free-form context pulled from proxy filings. DEF 14A ownership % may or may not be present.\n"
        "\n"
        "Respond ONLY with valid JSON matching this schema exactly:\n"
        "{\n"
        "  \"sitg_score\": 0-10 (number, one decimal allowed),\n"
        "  \"ceo_name\": \"full name or empty string\",\n"
        "  \"ownership_pct\": number-or-null (as percent, e.g. 7.3 not 0.073),\n"
        "  \"form4_buys_12m\": integer-or-null,\n"
        "  \"form4_sells_12m\": integer-or-null,\n"
        "  \"compensation_mix\": \"short phrase: cash-heavy / equity-heavy / balanced / unknown\",\n"
        "  \"archetype\": \"one of the rubric rows (e.g. 'Long-tenured professional CEO')\",\n"
        "  \"reasoning\": \"2-3 sentences citing specific signals (Form 4, proxy, ownership %). If data is incomplete, say so.\"\n"
        "}"
    ),
    "execution_risk_scorer": (
        "You are scoring execution risk for an equity on the 1-10 rubric from the Risk-Return-Ratio methodology (Step 2c). Higher = more execution risk. You ONLY discuss investment and corporate-execution topics.\n"
        "\n"
        "Rubric:\n"
        "  1-2  Regulated utility / steady-state compounder. Predictable cash flows, no major pivots, proven management.  tier=\"utility\"\n"
        "  3-4  Established industrial with modest growth initiatives. Some cyclicality but well-managed.  tier=\"industrial\"\n"
        "  5-6  Mid-stage growth company entering new markets. M&A integration underway. Some backlog concentration.  tier=\"mid_growth\"\n"
        "  7-8  High-growth company with major strategic pivots (new segments, large EPC contracts, platform bets). Margin ramp unproven.  tier=\"high_growth_pivot\"\n"
        "  9-10 Early-stage, unprofitable, binary outcome, or significant regulatory / geopolitical overhang.  tier=\"binary_early_stage\"\n"
        "\n"
        "Consider: sector stability, margin durability, backlog concentration, M&A integration status, platform / product pivots, regulatory exposure, geopolitical exposure, recent earnings misses, management tenure. Cite SPECIFIC signals in the reasoning \u2014 do not give a generic answer.\n"
        "\n"
        "You will receive a JSON block with: ticker, company name, sector, industry, revenue and EPS growth, beta, debt, free cash flow, recent news context.\n"
        "\n"
        "Respond ONLY with valid JSON matching this schema exactly:\n"
        "{\n"
        "  \"exec_score\": 1-10 (number, one decimal allowed),\n"
        "  \"profile_tier\": \"utility | industrial | mid_growth | high_growth_pivot | binary_early_stage\",\n"
        "  \"reasoning\": \"2-3 sentences citing specific execution signals (sector dynamics, recent earnings, pivots).\"\n"
        "}"
    ),
    "scorecard_verdict": (
        "You are writing a single-sentence verdict per ticker for an investor-facing Risk-Return scorecard. You ONLY discuss investment topics.\n"
        "\n"
        "You receive a JSON block with: ticker, preset (growth/value/income/balanced), ratio, signal (Exceptional / Strong buy / Favorable / Balanced / Caution / Avoid), return_score, risk_score, SITG score, and a short reason_hint describing why the numbers came out that way (e.g. \"PE stretch 172% above history\" or \"SITG 8/10 from family-owner-operator\").\n"
        "\n"
        "Rules:\n"
        "  - One sentence. Under 28 words.\n"
        "  - Pick the verdict label from: Strong, Favorable, Balanced, Stretched, Avoid.\n"
        "    \"Strong\"     \u2192 signal in {Exceptional, Strong buy}\n"
        "    \"Favorable\"  \u2192 signal == Favorable\n"
        "    \"Balanced\"   \u2192 signal == Balanced\n"
        "    \"Stretched\"  \u2192 signal == Caution\n"
        "    \"Avoid\"      \u2192 signal == Avoid\n"
        "  - Cite the SPECIFIC driver: PE stretch, growth momentum, SITG lift, D/E, etc. No generic language.\n"
        "  - Never say \"buy\" or \"sell\" in the reason \u2014 this is educational framing.\n"
        "\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        "  \"verdict\": \"Strong | Favorable | Balanced | Stretched | Avoid\",\n"
        "  \"one_line_reason\": \"one sentence citing the driver\"\n"
        "}"
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
    "video_veo_text_fallback": {
        "caption": "Lesson scene",
        "body": "Video generation was unavailable. Use the topic and captions as study notes.",
    },
    "decision_terminal_roadmap": {
        "bull_price_usd": 0,
        "base_price_usd": 0,
        "bear_price_usd": 0,
        "assumptions": [],
        "confidence_0_1": 0,
        "used_heuristic_fallback": True,
    },
    "sitg_scorer": {
        "sitg_score": 3,
        "ceo_name": "",
        "ownership_pct": None,
        "form4_buys_12m": None,
        "form4_sells_12m": None,
        "compensation_mix": "unknown",
        "archetype": "Most S&P 500 CEOs",
        "reasoning": "Insufficient insider / proxy data observed; defaulting to typical hired-professional-CEO tier.",
    },
    "execution_risk_scorer": {
        "exec_score": 5,
        "profile_tier": "mid_growth",
        "reasoning": "Insufficient qualitative context; defaulting to mid-stage-growth tier.",
    },
    "scorecard_verdict": {
        "verdict": "Balanced",
        "one_line_reason": "Risk/reward is roughly balanced at current prices; monitor catalysts.",
    },
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
        self._openrouter_pool = None
        self._sem = asyncio.Semaphore(LLM_MAX_CONCURRENCY)
        self._init_client()

    def _init_client(self):
        try:
            from openai import OpenAI  # noqa: F401 — presence check
        except Exception as e:
            logger.warning("[LLMClient] openai sdk unavailable: %s", redact_secrets_in_text(str(e)))
            return

        headers = {}
        if OPENROUTER_HTTP_REFERER:
            headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
        if OPENROUTER_X_TITLE:
            headers["X-Title"] = OPENROUTER_X_TITLE

        try:
            pool = get_or_create_openrouter_pool(OPENROUTER_BASE_URL, headers)
            if pool is None:
                if gemini_primary_enabled():
                    logger.info(
                        "[LLMClient] No OpenRouter key; streaming chat uses Gemini primary — model=%s",
                        GEMINI_FALLBACK_MODEL,
                    )
                else:
                    logger.warning("[LLMClient] No OPENROUTER_API_KEY configured. Using rule-based fallback.")
                return
            if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                guard_host("llm", OPENROUTER_BASE_URL)
            self._openrouter_pool = pool
            self._provider = "openrouter"
            self._backend = "openrouter"
            self._model = OPENROUTER_MODEL
            self._endpoint = OPENROUTER_BASE_URL
            logger.info("[LLMClient] Backend: OpenRouter direct — model: %s", OPENROUTER_MODEL)
            if gemini_primary_enabled():
                logger.info("[LLMClient] Gemini primary for chat — model=%s", GEMINI_FALLBACK_MODEL)
            elif gemini_llm_fallback_enabled():
                logger.info("[LLMClient] Gemini LLM fallback enabled — model=%s", GEMINI_FALLBACK_MODEL)
        except Exception as e:
            logger.warning("[LLMClient] OpenRouter init failed: %s", redact_secrets_in_text(str(e)))

    def _gemini_try_json_role(self, system: str, prompt: str, role: str) -> Optional[dict]:
        """
        Try to produce a JSON answer for ``role`` via Gemini. Returns None (so the
        caller can fall through) if Gemini isn't usable, returned empty, or blew up.

        The model is tier-selected from :data:`MODEL_TIER` via
        :func:`_gemini_model_for_role` — heavy roles use :data:`GEMINI_MODEL`,
        light roles use :data:`GEMINI_MODEL_LIGHT`.
        """
        if not gemini_usable_for_chat():
            return None
        model = _gemini_model_for_role(role)
        try:
            text = gemini_simple_completion_sync(
                system=system,
                user=prompt,
                max_tokens=LLM_MAX_TOKENS,
                temperature=0.3,
                json_mode=True,
                model=model,
            )
            if not (text or "").strip():
                return None
            logger.info("[LLMClient] role=%s via Gemini model=%s", role, model)
            return self._parse_json_response(text, role)
        except Exception as e:
            logger.warning(
                "[LLMClient] Gemini JSON call failed role=%s model=%s err=%s",
                role,
                model,
                redact_secrets_in_text(str(e)),
            )
            return None

    def _gemini_try_plain_text(self, system: str, user: str) -> Optional[str]:
        """Plain prose completion via Gemini-light (RAG polish, summarization)."""
        if not gemini_usable_for_chat():
            return None
        model = GEMINI_MODEL_LIGHT
        try:
            out = gemini_simple_completion_sync(
                system=system,
                user=user,
                max_tokens=min(900, LLM_MAX_TOKENS),
                temperature=0.2,
                json_mode=False,
                model=model,
            )
            out = (out or "").strip()
            if len(out) > 40:
                logger.info("[LLMClient] plain text via Gemini model=%s", model)
                return out
            return None
        except Exception as e:
            logger.warning(
                "[LLMClient] Gemini plain-text call failed model=%s err=%s",
                model,
                redact_secrets_in_text(str(e)),
            )
            return None

    # ── Inference ──────────────────────────────────────────────────────────

    def _resolve_system_prompt(self, role: str) -> tuple[str, str]:
        """
        Return (system_prompt_text, version). Reads from the RSPL resource
        registry when enabled; always falls through to the hardcoded
        ``AGENT_SYSTEM_PROMPTS`` dict so a registry outage never breaks LLM
        calls (see Phase A acceptance criterion #6).
        """
        default_body = "You are a finance analyst. Respond only in valid JSON."
        legacy_body = AGENT_SYSTEM_PROMPTS.get(role, default_body)
        try:
            from . import resource_registry as _rr
            if not _rr.registry_enabled():
                return legacy_body, "unversioned"
            reg = _rr.get_resource_registry()
            rec = reg.get(role)
            if rec is None or not rec.body:
                return legacy_body, "unversioned"
            return rec.body, rec.version
        except Exception as e:
            # Registry import/access must never interrupt inference.
            logger.debug("[LLMClient] registry lookup failed for role=%s: %s", role, e)
            return legacy_body, "unversioned"

    def _resolve_contract(
        self, role: str
    ) -> tuple[Optional[dict], Optional[dict]]:
        """
        Return ``(schema, fallback)`` for ``role`` from the RSPL registry.

        Both default to ``None`` when the registry is disabled, the role is not
        registered, or the record has no ``schema`` / ``fallback`` declared.
        Must never raise — callers treat ``(None, None)`` as "no contract".
        """
        try:
            from . import resource_registry as _rr
            if not _rr.registry_enabled():
                return None, None
            reg = _rr.get_resource_registry()
            rec = reg.get(role)
            if rec is None:
                return None, None
            return rec.schema, rec.fallback
        except Exception as e:
            logger.debug("[LLMClient] contract lookup failed for role=%s: %s", role, e)
            return None, None

    def _enforce_contract(
        self,
        result: dict,
        *,
        role: str,
        prompt_version: str,
        model: str,
    ) -> dict:
        """
        Run the universal :mod:`contract_validator` over an LLM-derived dict.

        Success path returns ``result`` unchanged. On a fatal schema violation
        (missing required key, wrong top-level type) the validator coerces to
        the resource's declared ``fallback`` so callers never see a broken
        contract. Every violation is forwarded to the validator's sink for
        later drift analytics. Never raises.
        """
        try:
            from . import contract_validator as _cv
            schema, fallback = self._resolve_contract(role)
            if not isinstance(schema, dict) or not schema:
                return result
            validator = _cv.get_contract_validator()
            payload, _viols, _coerced = validator.validate_result(
                result,
                role=role,
                schema=schema,
                fallback=fallback,
                version=prompt_version or "unversioned",
                model=model or "",
            )
            return payload if isinstance(payload, dict) else result
        except Exception as e:
            logger.debug(
                "[LLMClient] contract enforcement skipped for role=%s: %s",
                role, e,
            )
            return result

    def _provider_generate(
        self,
        role: str,
        prompt: str,
        *,
        body_override: Optional[str] = None,
        version_override: Optional[str] = None,
    ) -> tuple[dict, str]:
        """
        Call OpenRouter synchronously — run in a thread.

        Returns ``(result, prompt_version)``. Callers that only need the result
        (e.g. the legacy ``generate`` entry point) should discard the version.

        When ``body_override`` is supplied, the registry is NOT consulted and the
        given body is used as the system prompt. This path exists solely for the
        SEPL Evaluate operator (AGP §3.2 eps), which needs to run a candidate
        prompt body without registering it. ``version_override`` (defaults to
        ``"candidate"``) is stamped into the returned meta so lineage can tell
        inference-with-override apart from real active versions.
        """
        if body_override is not None:
            system = body_override
            prompt_version = version_override or "candidate"
        else:
            system, prompt_version = self._resolve_system_prompt(role)
        model = _model_for_role(role)
        fallback = lambda: (FALLBACK_TEMPLATES.get(role, {}), prompt_version)

        # Gemini-primary path (GEMINI_PRIMARY=1): route every role through Gemini
        # 3.1 Pro (heavy) or Gemini 3.1 Flash (light). On any Gemini failure we go
        # straight to ``FALLBACK_TEMPLATES`` — OpenRouter is intentionally NOT
        # consulted so all LLM spend lands on the Gemini account (user config:
        # "primary_with_local_fallback"). To re-enable OpenRouter as a fallback,
        # clear GEMINI_PRIMARY.
        if gemini_primary_enabled():
            g = self._gemini_try_json_role(system, prompt, role)
            if g is not None:
                g = self._enforce_contract(
                    g, role=role, prompt_version=prompt_version,
                    model=_gemini_model_for_role(role),
                )
                return g, prompt_version
            logger.info(
                "[LLMClient] role=%s Gemini-primary unavailable — using local fallback template",
                role,
            )
            return fallback()

        try:
            if self._openrouter_pool is None:
                return fallback()
            clients = self._openrouter_pool.sync_clients_for_request(
                should_try_other_openrouter_keys_on_429()
            )

            def _call_role(sync_client):
                if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                    with workload_scope("llm", "llm_inference"):
                        return sync_client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.3,
                            max_tokens=LLM_MAX_TOKENS,
                        )
                return sync_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=LLM_MAX_TOKENS,
                )

            completion, err = sync_failover_execute(
                clients,
                _call_role,
                exit_immediately_on_rate_limit=gemini_instant_openrouter_failover(),
            )
            if completion is not None:
                content = (completion.choices[0].message.content or "").strip()
                if not content:
                    logger.warning("[LLMClient] role=%s model=%s empty response", role, model)
                    return fallback()
                parsed = self._parse_json_response(content, role)
                parsed = self._enforce_contract(
                    parsed, role=role, prompt_version=prompt_version, model=model,
                )
                return parsed, prompt_version
            if err is not None:
                logger.warning(
                    "[LLMClient] call failed role=%s model=%s err=%s",
                    role,
                    model,
                    redact_secrets_in_text(str(err)),
                )
            g = self._gemini_try_json_role(system, prompt, role)
            if g is not None:
                g = self._enforce_contract(
                    g, role=role, prompt_version=prompt_version,
                    model=_gemini_model_for_role(role),
                )
                return g, prompt_version
            return fallback()
        except Exception as e:
            logger.warning(
                "[LLMClient] call failed role=%s model=%s err=%s",
                role,
                model,
                redact_secrets_in_text(str(e)),
            )
            g = self._gemini_try_json_role(system, prompt, role)
            if g is not None:
                g = self._enforce_contract(
                    g, role=role, prompt_version=prompt_version,
                    model=_gemini_model_for_role(role),
                )
                return g, prompt_version
            return fallback()

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
        """Async entry point — dispatches to OpenRouter in a thread.

        Backward-compatible: returns just the result dict. Callers that need
        the prompt version stamp (for RSPL lineage) should use
        :meth:`generate_with_meta` instead.
        """
        result, _version = await self.generate_with_meta(role, prompt)
        return result

    async def generate_with_meta(
        self, role: str, prompt: str
    ) -> tuple[dict, Dict[str, Any]]:
        """
        Same as :meth:`generate` but also returns a ``meta`` dict describing the
        resources used. ``meta`` always contains ``{"prompt_name": role,
        "prompt_version": str}`` — the version is ``"unversioned"`` when the
        registry is disabled or the resource was not found. Callers stamp this
        onto :class:`~backend.schemas.FactorResult` metadata or reflection rows
        so post-hoc analyses can tie outcomes to the exact prompt that produced
        them (AGP §3.1.2 "auditable lineage").
        """
        if self._provider == "openrouter":
            async with self._sem:
                result, version = await asyncio.to_thread(
                    self._provider_generate, role, prompt
                )
        else:
            # No provider wired — return configured fallback; still stamp version.
            _, version = self._resolve_system_prompt(role)
            result = FALLBACK_TEMPLATES.get(role, {})
        meta = {"prompt_name": role, "prompt_version": version}
        return result, meta

    async def generate_with_body_override(
        self,
        role: str,
        prompt: str,
        *,
        body: str,
        version_label: str = "candidate",
    ) -> tuple[dict, Dict[str, Any]]:
        """
        Inference path used by the SEPL Evaluate operator.

        Runs the same provider pipeline as :meth:`generate_with_meta` but with an
        explicit system-prompt ``body`` instead of the registry-resolved one.
        The registry is NEVER consulted and is NEVER mutated. ``version_label``
        is stamped into the returned meta dict — use something distinctive like
        ``"candidate"`` or ``"rollback-probe"`` so downstream audits can tell
        override calls apart from active-version calls.

        This method is the ONLY approved way for SEPL (or anything else) to
        exercise a non-registered prompt body against real inference.
        """
        if self._provider == "openrouter":
            async with self._sem:
                result, version = await asyncio.to_thread(
                    self._provider_generate,
                    role,
                    prompt,
                    body_override=body,
                    version_override=version_label,
                )
        else:
            result = FALLBACK_TEMPLATES.get(role, {})
            version = version_label
        return result, {"prompt_name": role, "prompt_version": version, "override": True}

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

    async def generate_swarm_synthesis(
        self,
        ticker: str,
        factor_results: list[dict],
        peer_summaries: Optional[dict] = None,
    ) -> dict:
        """Resolve disagreements among swarm factor agents."""
        factors_str = "\n".join(
            f"- {f['factor_name']}: signal={f['trading_signal']}, confidence={f['confidence']:.2f}, "
            f"status={f['status']}, rationale={f['rationale'][:200]}"
            for f in factor_results
        )
        peer_block = ""
        if peer_summaries:
            peer_block = "\nPeer factor rationale excerpts (cross-pollination):\n" + "\n".join(
                f"  - {k}: {(v or '')[:320]}" for k, v in peer_summaries.items()
            )
        prompt = (
            f"Ticker: {ticker.upper()}\n\n"
            f"Factor results from the swarm:\n{factors_str}\n\n"
            f"{peer_block}\n"
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

    async def generate_decision_terminal_roadmap(self, ticker: str, context: dict) -> dict:
        """3Y bull/base/bear prices for Decision Terminal — JSON only; fallback zeros trigger heuristics."""
        ctx = json.dumps(context, indent=2, default=str)
        if len(ctx) > 12000:
            ctx = ctx[:12000] + "\n…(truncated)"
        prompt = (
            f"Ticker: {ticker.upper()}\n\nContext JSON:\n{ctx}\n\n"
            "Produce bull/base/bear scenario prices and list explicit assumptions."
        )
        return await self.generate("decision_terminal_roadmap", prompt)

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
        """Single chat completion returning raw assistant text (no JSON parse).

        When ``GEMINI_PRIMARY=1``, this routes through Gemini (light model) and
        on any Gemini failure returns the untouched ``user`` string — same local
        fallback contract as the JSON inference path. OpenRouter is never called.
        """
        model = OPENROUTER_MODEL_LIGHT

        if gemini_primary_enabled():
            g = self._gemini_try_plain_text(system, user)
            if g is not None:
                return g
            return user

        try:
            if self._openrouter_pool is None:
                if gemini_primary_enabled():
                    g = self._gemini_try_plain_text(system, user)
                    if g is not None:
                        return g
                return user
            clients = self._openrouter_pool.sync_clients_for_request(
                should_try_other_openrouter_keys_on_429()
            )

            def _call_plain(sync_client):
                if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                    with workload_scope("llm", "llm_inference"):
                        return sync_client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            temperature=0.2,
                            max_tokens=min(900, LLM_MAX_TOKENS),
                        )
                return sync_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.2,
                    max_tokens=min(900, LLM_MAX_TOKENS),
                )

            completion, err = sync_failover_execute(
                clients,
                _call_plain,
                exit_immediately_on_rate_limit=gemini_instant_openrouter_failover(),
            )
            if completion is not None:
                out = (completion.choices[0].message.content or "").strip()
                return out if len(out) > 40 else user
            if err is not None:
                logger.warning(
                    "[LLMClient] plain_text_generate failed: %s",
                    redact_secrets_in_text(str(err)),
                )
            g = self._gemini_try_plain_text(system, user)
            if g is not None:
                return g
            return user
        except Exception as e:
            logger.warning(
                "[LLMClient] plain_text_generate failed: %s",
                redact_secrets_in_text(str(e)),
            )
            g = self._gemini_try_plain_text(system, user)
            if g is not None:
                return g
            return user

    async def stream_chat_plain(
        self,
        system: str,
        messages: list[dict],
        *,
        max_tokens: Optional[int] = None,
        tools: Optional[list] = None,
        tool_handlers: Optional[dict] = None,
        tool_trace_out: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[str]:
        """
        Stream assistant text tokens (plain prose, not JSON). Used by TradeTalk chat.
        Yields incremental text chunks; transparently handles autonomous tool execution if provided.
        If ``tool_trace_out`` is a list, each executed tool appends
        ``{name, arguments, outcome, error?}`` for logging and the evidence contract.
        """
        mt = max_tokens if max_tokens is not None else min(2048, LLM_MAX_TOKENS)
        model = OPENROUTER_MODEL_LIGHT
        _429_same_delay = float(os.environ.get("OPENROUTER_429_SAME_KEY_DELAY_SEC", "2.5"))
        _429_key_delay = float(os.environ.get("OPENROUTER_429_KEY_FAILOVER_DELAY_SEC", "1.0"))
        if self._openrouter_pool is None and not gemini_primary_enabled():
            yield (
                "Chat requires OPENROUTER_API_KEY. "
                "Configure the API key on the server to enable live responses."
            )
            return

        async_clients = (
            self._openrouter_pool.async_clients_for_request(
                should_try_other_openrouter_keys_on_429()
            )
            if self._openrouter_pool is not None
            else []
        )

        msgs = [{"role": "system", "content": system}]
        for m in messages:
            # Reconstruct valid message payloads, including raw tool turns if passed
            if m.get("role") in ("user", "assistant", "tool") and m.get("content") is not None:
                msgs.append(m)
            elif m.get("role") == "assistant" and m.get("tool_calls"):
                msgs.append(m)

        depth = 0
        while depth < 3:
            depth += 1
            tool_call_id = None
            tool_name = ""
            tool_args_str = ""
            is_tool_call = False

            stream_ok = False
            abort_openrouter_for_gemini = False
            ci = 0
            n_clients = len(async_clients)
            if gemini_primary_enabled():
                abort_openrouter_for_gemini = True
            while ci < n_clients and not stream_ok and not abort_openrouter_for_gemini:
                async_client = async_clients[ci]
                for attempt in range(2):
                    try:
                        kwargs = {
                            "model": model,
                            "messages": msgs,
                            "temperature": 0.35,
                            "max_tokens": mt,
                            "stream": True,
                        }
                        if tools:
                            kwargs["tools"] = tools

                        async with self._sem:
                            stream = await async_client.chat.completions.create(**kwargs)

                        async for chunk in stream:
                            delta = chunk.choices[0].delta if chunk.choices else None
                            if not delta:
                                continue

                            if getattr(delta, "tool_calls", None):
                                is_tool_call = True
                                tc = delta.tool_calls[0]
                                if getattr(tc, "id", None):
                                    tool_call_id = tc.id
                                if getattr(tc, "function", None):
                                    if getattr(tc.function, "name", None):
                                        tool_name += tc.function.name
                                    if getattr(tc.function, "arguments", None):
                                        tool_args_str += tc.function.arguments
                                continue

                            ch = getattr(delta, "content", None)
                            if ch:
                                yield ch

                        stream_ok = True
                        break
                    except Exception as e:
                        if not is_openrouter_rate_limit_error(e):
                            logger.warning(
                                "[LLMClient] stream_chat_plain failed: %s",
                                redact_secrets_in_text(str(e)),
                            )
                            yield f"\n\n[Chat error: {redact_secrets_in_text(str(e))[:200]}]"
                            return
                        if gemini_instant_openrouter_failover():
                            logger.info(
                                "[LLMClient] OpenRouter 429 — skipping backoff, immediate Gemini failover"
                            )
                            abort_openrouter_for_gemini = True
                            break
                        wait = rate_limit_sleep_seconds(e, _429_same_delay)
                        if attempt == 0:
                            logger.warning(
                                "[LLMClient] rate limited (429) key=%s attempt=0, sleeping %.1fs then retry same key",
                                ci,
                                wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        if ci < n_clients - 1:
                            extra = _429_key_delay
                            logger.warning(
                                "[LLMClient] rate limited (429) key=%s after retry, sleeping %.1fs then other key",
                                ci,
                                extra,
                            )
                            await asyncio.sleep(extra)
                            break
                        msg = (
                            "[Chat error: OpenRouter rate limit (429) on all configured keys. "
                            "Free models (e.g. `:free`) share strict upstream quotas — two API keys may both hit the same limit. "
                            "Wait a few minutes, or set OPENROUTER_MODEL to a paid slug on openrouter.ai/models, "
                            "or try another model.]"
                        )
                        yield f"\n\n{msg}\n"
                        return
                if abort_openrouter_for_gemini:
                    break
                if stream_ok:
                    break
                ci += 1

            gemini_error_only = False
            if not stream_ok and gemini_usable_for_chat():
                # TradeTalk chat = user-facing reasoning, so always heavy model.
                chat_model = GEMINI_MODEL
                logger.info(
                    "[LLMClient] stream_chat_plain via Gemini %smodel=%s",
                    "primary — " if gemini_primary_enabled() else "fallback — ",
                    chat_model,
                )
                try:
                    rest = msgs[1:] if len(msgs) > 1 else []
                    async with self._sem:
                        async for ev in gemini_fallback_chat_events(
                            system=system,
                            openai_messages=rest,
                            tools=tools,
                            max_tokens=mt,
                            temperature=0.35,
                            model=chat_model,
                        ):
                            if ev["kind"] == "error":
                                gemini_error_only = True
                                yield (
                                    f"\n\n[Chat error (Gemini fallback): "
                                    f"{redact_secrets_in_text(str(ev.get('message', 'unknown')))[:200]}]\n"
                                )
                                break
                            if ev["kind"] == "text":
                                stream_ok = True
                                t = ev.get("text") or ""
                                if t:
                                    yield t
                            elif ev["kind"] == "tool":
                                stream_ok = True
                                is_tool_call = True
                                tool_name = ev.get("name") or ""
                                tool_args_str = ev.get("args") or "{}"
                                tool_call_id = "call_gemini_fallback"
                except Exception as e:
                    logger.warning(
                        "[LLMClient] Gemini stream fallback failed: %s",
                        redact_secrets_in_text(str(e)),
                    )

            if not stream_ok and not gemini_error_only:
                yield (
                    "\n\n[Chat error: All OpenRouter keys failed for this request. "
                    "If you see 429, free-tier limits may apply — try a paid model or wait.]\n"
                )
                return

            if is_tool_call and tool_name and tool_handlers and tool_name in tool_handlers:
                args_dict = {}
                try:
                    args_dict = json.loads(tool_args_str)
                except Exception:
                    pass
                t = args_dict.get("ticker", "")
                # Show a context-appropriate loading message per tool
                if tool_name == "get_top_movers":
                    direction = args_dict.get("direction", "movers")
                    yield f"\n*[Loading verified S&P 500 {direction}...]*\n\n"
                elif tool_name == "get_market_news":
                    yield f"\n*[Fetching live headlines...]*\n\n"
                elif tool_name == "get_stock_quote" and t:
                    yield f"\n*[Fetching real-time quote for {t}...]*\n\n"
                elif tool_name == "get_price_history":
                    tk = args_dict.get("ticker", "")
                    if tk:
                        yield f"\n*[Loading historical prices for {tk}...]*\n\n"
                    else:
                        yield f"\n*[Fetching data...]*\n\n"
                else:
                    yield f"\n*[Fetching data...]*\n\n"

                try:
                    func = tool_handlers[tool_name]
                    result = await func(**args_dict) if asyncio.iscoroutinefunction(func) else func(**args_dict)
                except Exception as e:
                    result = f"Error executing {tool_name}: {e}"
                if tool_trace_out is not None:
                    out = classify_tool_result(str(result))
                    row: dict[str, Any] = {
                        "name": tool_name,
                        "arguments": args_dict,
                        "outcome": out,
                    }
                    if out == "error" or str(result).startswith("Error executing"):
                        row["error"] = str(result)[:500]
                    tool_trace_out.append(row)
                
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call_id or "call_0",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": tool_args_str
                        }
                    }]
                })
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id or "call_0",
                    "name": tool_name,
                    "content": str(result)
                })
                # Loop repeats! The next iteration will pass the tool result back to the LLM.
            else:
                break

    async def generate_sitg_score(self, ticker: str, sitg_context: dict) -> dict:
        """
        Risk-Return-Ratio Step 2e — score CEO / founder skin-in-the-game.

        ``sitg_context`` should include everything the scorer persona needs:
        ticker, company_name, ceo_name (if known), insider_buy_count_12m,
        insider_sell_count_12m, insider_net_shares_12m, held_percent_insiders,
        plus any DEF 14A ownership % or free-text signals harvested from
        proxy filings. Returns the parsed JSON per the ``sitg_scorer`` schema.
        """
        ctx = json.dumps(sitg_context, indent=2, default=str)
        if len(ctx) > 12000:
            ctx = ctx[:12000] + "\n…(truncated)"
        prompt = (
            f"Ticker: {ticker.upper()}\n\nContext JSON:\n{ctx}\n\n"
            "Score the CEO / founder on the Skin-In-The-Game rubric."
        )
        return await self.generate("sitg_scorer", prompt)

    async def generate_execution_risk_score(self, ticker: str, exec_context: dict) -> dict:
        """Risk-Return-Ratio Step 2c — score execution risk (1-10)."""
        ctx = json.dumps(exec_context, indent=2, default=str)
        if len(ctx) > 12000:
            ctx = ctx[:12000] + "\n…(truncated)"
        prompt = (
            f"Ticker: {ticker.upper()}\n\nContext JSON:\n{ctx}\n\n"
            "Score execution risk on the 1-10 rubric."
        )
        return await self.generate("execution_risk_scorer", prompt)

    async def generate_scorecard_verdict(self, ticker: str, verdict_context: dict) -> dict:
        """Risk-Return-Ratio Step 8 — one-sentence verdict per ticker."""
        ctx = json.dumps(verdict_context, indent=2, default=str)
        if len(ctx) > 8000:
            ctx = ctx[:8000] + "\n…(truncated)"
        prompt = (
            f"Ticker: {ticker.upper()}\n\nContext JSON:\n{ctx}\n\n"
            "Write the one-sentence verdict."
        )
        return await self.generate("scorecard_verdict", prompt)

    async def generate_rag_polish(self, context_label: str, draft: str) -> str:
        """
        Phase 5 — tighten data-lake summaries for embedding/RAG (OpenRouter chat model).
        Falls back to draft when API key missing or on error.
        """
        if self._provider != "openrouter" or self._openrouter_pool is None:
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
