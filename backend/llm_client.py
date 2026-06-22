"""
LLM Client — OpenRouter primary, Gemini 3.5 Flash fallback, rule-based last resort.

  1. OpenRouter — ``OPENROUTER_API_KEY``, ``OPENROUTER_MODEL`` / ``OPENROUTER_MODEL_LIGHT``.
  2. Gemini (Google AI Studio) — when ``GEMINI_LLM_FALLBACK=1`` and ``GEMINI_API_KEY`` is set.
  3. Rule-based templates — only for non-verdict roles when all providers fail.

Agent policy guardrails (defense-in-depth) are enforced via
agent_policy_guardrails when enabled.
"""
import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, AsyncIterator
from .agent_policy_guardrails import (
    guard_host,
    is_enabled as policy_guardrails_enabled,
    redact_secrets_in_text,
    workload_scope,
)
from .openrouter_pool import (
    collect_nvidia_llm_api_keys,
    collect_openrouter_api_keys,
    get_or_create_openrouter_pool,
    get_or_create_llm_openai_compatible_pool,
    is_openrouter_rate_limit_error,
    rate_limit_sleep_seconds,
    resolve_llm_http_provider,
    should_try_other_openrouter_keys_on_429,
    sync_failover_execute,
)
from .chat_evidence_contract import classify_tool_result
from .data_errors import InsufficientDataError
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
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "minimax/minimax-m3")
NVIDIA_MODEL_LIGHT = os.environ.get("NVIDIA_MODEL_LIGHT", "minimax/minimax-m3")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
OPENROUTER_MODEL_LIGHT = os.environ.get("OPENROUTER_MODEL_LIGHT", "google/gemma-4-31b-it:free")
OPENROUTER_HTTP_REFERER = os.environ.get("OPENROUTER_HTTP_REFERER", "")
OPENROUTER_X_TITLE = os.environ.get("OPENROUTER_X_TITLE", "TradeTalk App")

GUARDRAILS_ENABLE = os.environ.get("GUARDRAILS_ENABLE", "1").strip() != "0"
LLM_MAX_CONCURRENCY = max(1, int(os.environ.get("LLM_MAX_CONCURRENCY", "6")))
# OpenRouter pre-reserves credits from max_tokens; 16k × parallel debate agents
# triggers HTTP 402 on modest balances. 1500 matches backend/.env.example and is
# ample for JSON debate/swarm roles; chat can override per call.
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
    "daily_brief_batch":      "light",
    "news_impact_classifier": "light",
}

def _tier_for_role(role: str) -> str:
    return MODEL_TIER.get(role, "heavy")


def _model_for_role(role: str) -> str:
    return OPENROUTER_MODEL_LIGHT if _tier_for_role(role) == "light" else OPENROUTER_MODEL


def _http_openai_model_for_role(_provider: str, role: str) -> str:
    return _model_for_role(role)


def _gemini_model_for_role(role: str) -> str:
    """
    Pick the Gemini model for a given role using the same heavy/light mapping as
    OpenRouter. Both tiers default to ``gemini-3.5-flash`` — OpenRouter is primary
    and Gemini 3.5 Flash is the fallback.
    """
    return resolve_gemini_model(_tier_for_role(role))

# ── Finance-domain system prompts locked per agent role ──────────────────────
AGENT_SYSTEM_PROMPTS = {
    "bull": (
        "You are an aggressive growth investor and bull case analyst on a Wall Street panel. "
        "Your job is to identify the strongest bullish catalysts for a given stock using the "
        "data and historical context provided. Cite specific numbers. Be optimistic but grounded in data. "
        "If customer concentration data is present (e.g., from SEC 10-K), argue why it might be acceptable or positive (strategic anchor, expanding revenue, strong retention/NRR, declining concentration over time). "
        "ONLY discuss investment and financial topics. "
        "Respond ONLY with valid JSON: {\"headline\": \"one bold sentence\", "
        "\"key_points\": [\"point1\", \"point2\", \"point3\"], \"confidence\": 0.0-1.0}"
    ),
    "bear": (
        "You are a risk-averse fund manager and bear case analyst on a Wall Street panel. "
        "Your job is to identify the most serious risks and downside scenarios for a given stock "
        "using the data and historical context provided. Be cautious and precise. "
        "If customer concentration data is present (e.g., from SEC 10-K), argue why it is dangerous (pricing renegotiation risk, top customer leaving, weakening retention, high dependence). "
        "ONLY discuss investment and financial topics. "
        "Respond ONLY with valid JSON: {\"headline\": \"one bold sentence\", "
        "\"key_points\": [\"point1\", \"point2\", \"point3\"], \"confidence\": 0.0-1.0}"
    ),
    "macro": (
        "You are a senior macroeconomist on a Wall Street panel. "
        "Evaluate how the current macro environment — interest rates, inflation, credit stress, "
        "market regime — affects the investment case for a stock. "
        "Every claim or key point MUST be strictly grounded in the provided macro data with valid, accurate citations. Do not make unverified assertions. "
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
        "Be decisive, balanced, and cite the weight of evidence. If customer concentration risk is debated, classify it as a Strength, Manageable risk, Material risk, or Thesis-breaking risk. "
        "ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: {\"verdict\": \"STRONG BUY|BUY|NEUTRAL|SELL|STRONG SELL\", "
        "\"summary\": \"1-2 sentence ultra-concise, punchy plain-English explanation of the key reasons. Keep it under 60 words for quick scanning.\"}"
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
    "small_cap_analyst": (
        "You are a growth-stage equity analyst specializing in Small Cap and Micro Cap companies "
        "that may be pre-profit or early-profit. Standard P/E and Graham metrics do NOT apply. "
        "Evaluate using six growth-stage criteria only:\n"
        "1) Profitability Runway — credible path to profitability within 2-3 years (not 5+ year moonshots).\n"
        "2) Revenue & Margin Momentum — revenue growth and gross/operating margins improving over time.\n"
        "3) Problem-Solution Fit — solves a near-term real bottleneck, not speculative science fiction.\n"
        "4) Institutional Backing — credible institutions or funds on the register with meaningful ownership.\n"
        "5) Founding Team Stability — founders/operators still leading, focused, capable.\n"
        "6) Product Moat — differentiated product with a creative moat that can monetize in 2-5 years.\n"
        "Score each signal green (strong), yellow (mixed/uncertain), or red (weak). "
        "Be honest when data is missing — use yellow, not green. ONLY discuss investment topics. "
        "Also summarize revenue streams (product lines/segments) with up to 5 annual years of revenue "
        "and gross/operating margin when inferable from filings, business summary, or provided financial rows. "
        "List major enterprise deals/partnerships/customer wins from news headlines when amounts or counterparties "
        "are mentioned; use amount_label when exact USD is unclear. "
        "Respond ONLY with valid JSON: {\"signals\": [{\"label\": \"Profitability Runway\", "
        "\"score\": \"green|yellow|red\", \"headline\": \"one sentence\", \"detail\": \"2-3 sentences\"}, "
        "... six total with exact labels: Profitability Runway, Revenue & Margin Momentum, "
        "Problem-Solution Fit, Institutional Backing, Founding Team Stability, Product Moat], "
        "\"overall_verdict\": \"Compelling|Watch|Avoid\", \"overall_rationale\": \"2-3 sentences\", "
        "\"revenue_streams\": [{\"name\": \"stream name\", \"latest_share_pct\": number|null, "
        "\"years\": [{\"year\": \"2024\", \"revenue_usd\": number|null, \"gross_margin_pct\": number|null, "
        "\"operating_margin_pct\": number|null}]}], "
        "\"major_deals\": [{\"partner\": \"counterparty\", \"deal_type\": \"contract|partnership|customer win\", "
        "\"amount_usd\": number|null, \"amount_label\": \"$50M multi-year|Undisclosed\", \"year\": 2025, "
        "\"summary\": \"one sentence\", \"predictability_note\": \"why this improves visibility\"}]}"
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
        "  \"reasoning\": \"2-3 sentences citing specific signals (Form 4, proxy, ownership %). If data is incomplete, say so.\",\n"
        "  \"ceo_base_salary\": number-or-null (estimated base salary in raw USD, e.g. 1000000. Use 1000000 as default if unknown),\n"
        "  \"sitg_value\": number-or-null (estimated market value of stock owned in raw USD, e.g. 45000000),\n"
        "  \"sitg_multiple\": number-or-null (SITG Multiple = SITG Value / CEO Base Salary, e.g. 45.0),\n"
        "  \"sitg_percentile_tier\": \"string-or-null: 'Founder-Level SITG' if multiple >= 100; 'Most S&P 500 CEOs' if 5 <= multiple < 100; 'Below Average SITG' if multiple < 5\"\n"
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
        "CUSTOMER CONCENTRATION PENALTY: Assess customer concentration risk using SEC 10-K data (if provided). Penalize the execution risk score upward by considering:\n"
        "  - Top Customer Dependency (>20% is high risk, >35% is severe risk).\n"
        "  - Concentration Trend (is dependence increasing?).\n"
        "  - Retention Weakness (e.g., NRR <100% or churn risks).\n"
        "Explicitly adjust your score based on these factors.\n"
        "\n"
        "You will receive a JSON block with: ticker, company name, sector, industry, revenue and EPS growth, beta, debt, free cash flow, recent news context, and potentially sec_10k_context.\n"
        "\n"
        "Respond ONLY with valid JSON matching this schema exactly:\n"
        "{\n"
        "  \"exec_score\": 1-10 (number, one decimal allowed),\n"
        "  \"profile_tier\": \"utility | industrial | mid_growth | high_growth_pivot | binary_early_stage\",\n"
        "  \"reasoning\": \"2-3 sentences citing specific execution signals (sector dynamics, recent earnings, pivots).\"\n"
        "}"
    ),
    "daily_brief_batch": (
        "You are a senior equity strategist writing end-of-day brief verdicts for top market movers. "
        "You ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: "
        "{\"rows\": [{\"symbol\": \"TICKER\", \"verdict\": \"Strong Buy|Buy|Hold|Sell\", "
        "\"one_line_reason\": \"one sentence\"}]}"
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
    "news_impact_classifier": (
        "You are a financial news analyst classifying the investment impact of a single headline. "
        "ONLY discuss investment topics. "
        "Respond ONLY with valid JSON: "
        "{\"sentiment\": \"positive|negative|neutral\", "
        "\"impact\": \"1-2 sentence explanation of the investment significance\"}"
    ),
    "gold_advisor": (
        "You are a precious-metals allocator advisor for LONG-TERM investors (not day traders). "
        "You receive a JSON snapshot: macro (VIX, 10Y TIPS real yield, nominal 10Y, DXY, gold futures), "
        "pre-computed daily technicals (RSI, MACD, Bollinger, ATR, pivots — do NOT recalculate), "
        "headline sentiment score, and calendar hints. "
        "Explain how real yields and the dollar typically relate to gold; be nuanced — correlation breaks. "
        "Ensure that every level, driver, or technical indicator mentioned is strictly grounded in the input JSON snapshot with direct reference and accurate citations. "
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

# ── Truthful-data contract ────────────────────────────────────────────────────
# Roles whose output is (or directly feeds) a user-facing verdict/analysis.
# When no real LLM output can be produced for these roles we raise
# InsufficientDataError instead of returning a canned template — the app must
# say "insufficient data" rather than fabricate a final result.
VERDICT_ROLES = frozenset({
    "bull",
    "bear",
    "macro",
    "value",
    "momentum",
    "moderator",
    "swarm_synthesizer",
    "swarm_analyst",
    "small_cap_analyst",
    "scorecard_verdict",
    "gold_advisor",
    "sitg_scorer",
    "execution_risk_scorer",
    "new_revenue_engine_scorer",
})

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
    "small_cap_analyst": {
        "signals": [
            {
                "label": "Profitability Runway",
                "score": "yellow",
                "headline": "Profitability timeline is unclear from available filings.",
                "detail": "Revenue may be growing but losses and cash burn need closer review before assuming a 2-3 year path to profit.",
            },
            {
                "label": "Revenue & Margin Momentum",
                "score": "yellow",
                "headline": "Growth trajectory requires confirmation across multiple periods.",
                "detail": "Use quarterly revenue and margin trends to verify acceleration rather than a single YoY snapshot.",
            },
            {
                "label": "Problem-Solution Fit",
                "score": "yellow",
                "headline": "Business addresses a real market need but execution risk remains.",
                "detail": "Assess whether demand is near-term and measurable versus a long-dated speculative bet.",
            },
            {
                "label": "Institutional Backing",
                "score": "yellow",
                "headline": "Institutional ownership data is limited or mixed.",
                "detail": "Quality of holders matters more than count — look for reputable funds with sustained positions.",
            },
            {
                "label": "Founding Team Stability",
                "score": "yellow",
                "headline": "Leadership continuity cannot be fully verified from public data.",
                "detail": "Confirm founders or operator-CEOs remain engaged without major distraction or turnover.",
            },
            {
                "label": "Product Moat",
                "score": "yellow",
                "headline": "Differentiation is plausible but not yet proven at scale.",
                "detail": "Evaluate whether the product solves a bottleneck competitors cannot easily replicate in 2-5 years.",
            },
        ],
        "overall_verdict": "Watch",
        "overall_rationale": "Mixed growth-stage signals — standard valuation metrics do not apply; diligence on runway, holders, and team is required.",
        "revenue_streams": [],
        "major_deals": [],
    },
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
        "ceo_base_salary": None,
        "sitg_value": None,
        "sitg_multiple": None,
        "sitg_percentile_tier": None,
    },
    "execution_risk_scorer": {
        "exec_score": 5,
        "profile_tier": "mid_growth",
        "reasoning": "Insufficient qualitative context; defaulting to mid-stage-growth tier.",
    },
    "new_revenue_engine_scorer": {
        "financial_traction_score": 50,
        "customer_adoption_score": 50,
        "management_commitment_score": 50,
        "market_opportunity_score": 50,
        "monetization_clarity_score": 50,
        "execution_capacity_score": 50,
        "reasoning": "Insufficient qualitative context; defaulting to baseline scores.",
    },
    "scorecard_verdict": {
        "verdict": "Balanced",
        "one_line_reason": "Risk/reward is roughly balanced at current prices; monitor catalysts.",
    },
    "daily_brief_batch": {
        "rows": [],
    },
    "news_impact_classifier": {
        "sentiment": "neutral",
        "impact": None,
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
    "ingestion_judge": {
        "keep_as": "discard",
        "reusability": 0.0,
        "durability": "ephemeral",
        "tags": [],
        "linked_symbols": [],
        "linked_event": None,
        "flow_date": None,
        "one_line_reason": "Default fallback: discard.",
    },
}


def _fallback_template_or_raise(role: str) -> dict:
    """
    Truthful-data contract: verdict-producing roles must never be answered
    with a canned template. Non-verdict roles (video text, empty batch rows,
    ingestion judging, ...) may still degrade gracefully.
    """
    if role in VERDICT_ROLES:
        raise InsufficientDataError(
            "llm",
            f"LLM analysis unavailable for role '{role}' — refusing to return "
            "a fabricated verdict. Try again when the model provider is reachable.",
            missing=[f"llm_output:{role}"],
        )
    return FALLBACK_TEMPLATES.get(role, {})


class LLMClient:
    """
    Async LLM wrapper — OpenRouter + optional Gemini fallback.
    Policy guardrails (in-process, defense-in-depth) are enforced when enabled.
    Uses asyncio.to_thread for all blocking HTTP calls.
    """

    def __init__(self):
        self._backend = "fallback"
        self._provider = "fallback"
        self._llm_http_provider = "fallback"
        self._model = ""
        self._endpoint = ""
        self._nvidia_pool = None
        self._openrouter_pool = None
        # One semaphore per running event loop — ``asyncio.run`` in tests closes loops;
        # Starlette uses a different loop for TestClient/production requests.
        self._sem_by_loop: dict[int, asyncio.Semaphore] = {}
        self._sem_lock = threading.Lock()
        self._init_client()

    def _sem_for_current_loop(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        key = id(loop)
        with self._sem_lock:
            sem = self._sem_by_loop.get(key)
            if sem is None:
                sem = asyncio.Semaphore(LLM_MAX_CONCURRENCY)
                self._sem_by_loop[key] = sem
            return sem

    def _init_client(self):
        self._llm_http_provider = "fallback"
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
        cf_id = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
        cf_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
        if cf_id:
            headers["CF-Access-Client-Id"] = cf_id
        if cf_secret:
            headers["CF-Access-Client-Secret"] = cf_secret

        try:
            nv_keys = collect_nvidia_llm_api_keys()
            if nv_keys:
                try:
                    self._nvidia_pool = get_or_create_llm_openai_compatible_pool(NVIDIA_BASE_URL, headers, nv_keys)
                    if self._nvidia_pool:
                        if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                            guard_host("llm", NVIDIA_BASE_URL)
                        logger.info("[LLMClient] NVIDIA pool initialized with model: %s", NVIDIA_MODEL)
                except Exception as e:
                    logger.warning("[LLMClient] NVIDIA pool init failed: %s", redact_secrets_in_text(str(e)))

            or_keys = collect_openrouter_api_keys()
            if or_keys:
                try:
                    self._openrouter_pool = get_or_create_openrouter_pool(OPENROUTER_BASE_URL, headers)
                    if self._openrouter_pool:
                        if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                            guard_host("llm", OPENROUTER_BASE_URL)
                        logger.info("[LLMClient] OpenRouter pool initialized with model: %s", OPENROUTER_MODEL)
                except Exception as e:
                    logger.warning("[LLMClient] OpenRouter pool init failed: %s", redact_secrets_in_text(str(e)))

            # Resolve primary HTTP provider
            if self._nvidia_pool is not None:
                self._llm_http_provider = "nvidia"
                self._provider = "nvidia"
                self._backend = "nvidia"
                self._model = NVIDIA_MODEL
                self._endpoint = NVIDIA_BASE_URL
            elif self._openrouter_pool is not None:
                self._llm_http_provider = "openrouter"
                self._provider = "openrouter"
                self._backend = "openrouter"
                self._model = OPENROUTER_MODEL
                self._endpoint = OPENROUTER_BASE_URL

            if self._llm_http_provider == "fallback":
                if gemini_primary_enabled():
                    logger.info(
                        "[LLMClient] No HTTP LLM key; streaming chat uses Gemini primary — model=%s",
                        GEMINI_FALLBACK_MODEL,
                    )
                else:
                    logger.warning(
                        "[LLMClient] No API keys configured. Using rule-based fallback."
                    )
            else:
                logger.info("[LLMClient] Primary Backend: %s — model: %s", self._provider, self._model)
                if gemini_primary_enabled():
                    logger.info("[LLMClient] Gemini primary for chat — model=%s", GEMINI_FALLBACK_MODEL)
                elif gemini_llm_fallback_enabled():
                    logger.info("[LLMClient] Gemini LLM fallback enabled — model=%s", GEMINI_FALLBACK_MODEL)
        except Exception as e:
            logger.warning("[LLMClient] LLM HTTP init failed: %s", redact_secrets_in_text(str(e)))

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
                max_tokens=LLM_MAX_TOKENS,
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
        Call OpenRouter/NVIDIA synchronously — run in a thread.

        Returns ``(result, prompt_version)``. Callers that only need the result
        (e.g. the legacy ``generate`` entry point) should discard the version.
        """
        if body_override is not None:
            system = body_override
            prompt_version = version_override or "candidate"
        else:
            system, prompt_version = self._resolve_system_prompt(role)

        def fallback():
            return (_fallback_template_or_raise(role), prompt_version)

        # Gemini-primary (GEMINI_PRIMARY=1): try Gemini first, then HTTP providers
        gemini_primary_first = gemini_primary_enabled()
        if gemini_primary_first:
            g = self._gemini_try_json_role(system, prompt, role)
            if g is not None:
                g = self._enforce_contract(
                    g, role=role, prompt_version=prompt_version,
                    model=_gemini_model_for_role(role),
                )
                return g, prompt_version
            logger.info(
                "[LLMClient] role=%s Gemini-primary failed — trying HTTP cascade",
                role,
            )

        try:
            # Build list of HTTP pools to try in order
            http_cascades = []
            if self._nvidia_pool is not None:
                http_cascades.append(("nvidia", self._nvidia_pool))
            if self._openrouter_pool is not None:
                http_cascades.append(("openrouter", self._openrouter_pool))

            for prov_name, pool in http_cascades:
                if prov_name == "nvidia":
                    model = NVIDIA_MODEL_LIGHT if _tier_for_role(role) == "light" else NVIDIA_MODEL
                    endpoint = NVIDIA_BASE_URL
                else:
                    model = OPENROUTER_MODEL_LIGHT if _tier_for_role(role) == "light" else OPENROUTER_MODEL
                    endpoint = OPENROUTER_BASE_URL

                clients = pool.sync_clients_for_request(
                    should_try_other_openrouter_keys_on_429()
                )

                # Exit instantly on 429 if we have a subsequent option in the cascade
                has_subsequent = (prov_name == "nvidia" and self._openrouter_pool is not None) or gemini_usable_for_chat()
                exit_immediately = gemini_instant_openrouter_failover() if has_subsequent else False

                last_err: Optional[Exception] = None
                for attempt in range(2):
                    def _call_role(sync_client, _model=model):
                        if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                            with workload_scope("llm", "llm_inference"):
                                return sync_client.chat.completions.create(
                                    model=_model,
                                    messages=[
                                        {"role": "system", "content": system},
                                        {"role": "user", "content": prompt},
                                    ],
                                    temperature=0.3,
                                    max_tokens=LLM_MAX_TOKENS,
                                )
                        return sync_client.chat.completions.create(
                            model=_model,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.3,
                            max_tokens=LLM_MAX_TOKENS,
                        )

                    start_time = time.time()
                    completion, err = sync_failover_execute(
                        clients,
                        _call_role,
                        exit_immediately_on_rate_limit=exit_immediately,
                    )
                    latency = time.time() - start_time
                    if completion is not None:
                        content = (completion.choices[0].message.content or "").strip()
                        if not content:
                            logger.warning(
                                "[LLMClient] role=%s provider=%s model=%s empty response",
                                role,
                                prov_name,
                                model,
                            )
                            break

                        prompt_tokens = 0
                        completion_tokens = 0
                        try:
                            if hasattr(completion, "usage") and completion.usage:
                                prompt_tokens = getattr(completion.usage, "prompt_tokens", 0) or 0
                                completion_tokens = getattr(completion.usage, "completion_tokens", 0) or 0
                        except Exception:
                            pass

                        from .decision_ledger import log_llm_api_call
                        log_llm_api_call(
                            prompt_text=f"{system}\n{prompt}" if system else prompt,
                            model=model,
                            latency=latency,
                            response_text=content,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            api_url=endpoint,
                        )

                        parsed = self._parse_json_response(content, role)
                        if parsed is None:
                            logger.warning(
                                "[LLMClient] role=%s provider=%s model=%s unparseable JSON",
                                role,
                                prov_name,
                                model,
                            )
                            break
                        parsed = self._enforce_contract(
                            parsed,
                            role=role,
                            prompt_version=prompt_version,
                            model=model,
                        )
                        return parsed, prompt_version
                    last_err = err
                    if err is not None and is_openrouter_rate_limit_error(err) and attempt == 0 and not exit_immediately:
                        wait = rate_limit_sleep_seconds(err, 2.5)
                        logger.warning(
                            "[LLMClient] role=%s provider=%s model=%s rate limited, retry in %.1fs",
                            role,
                            prov_name,
                            model,
                            wait,
                        )
                        time.sleep(wait)
                        continue
                    break

                if last_err is not None:
                    logger.warning(
                        "[LLMClient] Provider %s call failed role=%s model=%s err=%s",
                        prov_name,
                        role,
                        model,
                        redact_secrets_in_text(str(last_err)),
                    )

            # Gemini fallback (if not primary first)
            if not gemini_primary_first:
                g = self._gemini_try_json_role(system, prompt, role)
                if g is not None:
                    g = self._enforce_contract(
                        g, role=role, prompt_version=prompt_version,
                        model=_gemini_model_for_role(role),
                    )
                    return g, prompt_version

            return fallback()
        except InsufficientDataError:
            raise
        except Exception as e:
            logger.warning(
                "[LLMClient] call failed role=%s err=%s",
                role,
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

    def _parse_json_response(self, content: str, role: str) -> Optional[dict]:
        """Strip markdown fences and parse JSON. Return fallback on failure."""
        # Remove <think>...</think> blocks if the model includes reasoning tags.
        import ast
        import re

        def _extract_first_balanced_json_object(text: str) -> Optional[str]:
            start = text.find("{")
            if start < 0:
                return None
            depth = 0
            in_str = False
            esc = False
            for i, ch in enumerate(text[start:], start=start):
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
            return None

        def _try_json_parse(candidate: str) -> Optional[dict]:
            c = (candidate or "").strip()
            if not c:
                return None
            try:
                out = json.loads(c)
                return out if isinstance(out, dict) else None
            except Exception:
                pass
            # Repair common LLM JSON defects.
            repaired = c
            repaired = repaired.replace("\u201c", '"').replace("\u201d", '"')
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
            try:
                out = json.loads(repaired)
                return out if isinstance(out, dict) else None
            except Exception:
                pass
            # Last-ditch: Python literal-esque dicts using single quotes.
            try:
                py_like = repaired.replace("null", "None").replace("true", "True").replace("false", "False")
                out = ast.literal_eval(py_like)
                if isinstance(out, dict):
                    return json.loads(json.dumps(out))
            except Exception:
                return None
            return None

        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Strip markdown code fences
        if "```" in content:
            parts = content.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                parsed = _try_json_parse(part)
                if parsed is not None:
                    return parsed
        parsed = _try_json_parse(content)
        if parsed is not None:
            return parsed
        candidate = _extract_first_balanced_json_object(content)
        if candidate:
            parsed = _try_json_parse(candidate)
            if parsed is not None:
                return parsed
        logger.warning(f"[LLMClient] JSON parse failed for role={role}. Raw: {content[:200]}")
        # Verdict roles: return None so _provider_generate can try Gemini before
        # raising InsufficientDataError (OpenRouter may return prose/thinking tags).
        if role in VERDICT_ROLES:
            return None
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
            async with self._sem_for_current_loop():
                result, version = await asyncio.to_thread(
                    self._provider_generate, role, prompt
                )
        else:
            # No provider wired — verdict roles raise (truthful-data contract);
            # non-verdict roles still degrade to templates. Version still stamped.
            _, version = self._resolve_system_prompt(role)
            result = _fallback_template_or_raise(role)
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
            async with self._sem_for_current_loop():
                result, version = await asyncio.to_thread(
                    self._provider_generate,
                    role,
                    prompt,
                    body_override=body,
                    version_override=version_label,
                )
        else:
            result = _fallback_template_or_raise(role)
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
            f"Synthesise these into a final investment verdict. In the 'summary', provide a highly quantitative, "
            f"punchy summary citing specific metrics (like P/E, growth, ROE, FCF, macro regimes, VIX) from the arguments, "
            f"focusing on the core tension. Keep it under 70 words."
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

    async def generate_small_cap_analysis(self, metrics: dict) -> dict:
        """Growth-stage assessment for small / micro cap tickers — six signal JSON contract."""
        ctx = json.dumps(metrics, indent=2, default=str)
        if len(ctx) > 14000:
            ctx = ctx[:14000] + "\n…(truncated)"
        prompt = (
            f"Ticker: {(metrics.get('ticker') or '').upper()}\n"
            f"Cap bucket: {metrics.get('cap_bucket')}\n"
            f"Sector: {metrics.get('sector')} / {metrics.get('industry')}\n\n"
            f"Quantitative and qualitative inputs JSON:\n{ctx}\n\n"
            "Apply the six growth-stage criteria. Do NOT use P/E or large-cap value rules. "
            "Use company_revenue_history_5y and segment_revenue_streams (yfinance) for baseline revenue. "
            "When fincrawler_sec_10k_excerpt / fincrawler_sec_10q_excerpt are present, use them for "
            "segment mix, customer concentration, and backlog visibility. "
            "Use news_headlines, fincrawler_news_summaries, and fincrawler_sec_8k_excerpt for major_deals."
        )
        return await self.generate("small_cap_analyst", prompt)

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
        fallback contract as the JSON inference path. OpenRouter/NVIDIA is never called.
        """
        if gemini_primary_enabled():
            g = self._gemini_try_plain_text(system, user)
            if g is not None:
                return g
            return user

        try:
            # Build list of HTTP pools to try in order
            http_cascades = []
            if self._nvidia_pool is not None:
                http_cascades.append(("nvidia", self._nvidia_pool))
            if self._openrouter_pool is not None:
                http_cascades.append(("openrouter", self._openrouter_pool))

            for prov_name, pool in http_cascades:
                if prov_name == "nvidia":
                    model = NVIDIA_MODEL_LIGHT
                    endpoint = NVIDIA_BASE_URL
                else:
                    model = OPENROUTER_MODEL_LIGHT
                    endpoint = OPENROUTER_BASE_URL

                clients = pool.sync_clients_for_request(
                    should_try_other_openrouter_keys_on_429()
                )

                # Exit instantly on 429 if we have a subsequent option in the cascade
                has_subsequent = (prov_name == "nvidia" and self._openrouter_pool is not None) or gemini_usable_for_chat()
                exit_immediately = gemini_instant_openrouter_failover() if has_subsequent else False

                for attempt in range(2):
                    def _call_plain(sync_client, _model=model):
                        if GUARDRAILS_ENABLE and policy_guardrails_enabled():
                            with workload_scope("llm", "llm_inference"):
                                return sync_client.chat.completions.create(
                                    model=_model,
                                    messages=[
                                        {"role": "system", "content": system},
                                        {"role": "user", "content": user},
                                    ],
                                    temperature=0.2,
                                    max_tokens=LLM_MAX_TOKENS,
                                )
                        return sync_client.chat.completions.create(
                            model=_model,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            temperature=0.2,
                            max_tokens=LLM_MAX_TOKENS,
                        )

                    start_time = time.time()
                    completion, err = sync_failover_execute(
                        clients,
                        _call_plain,
                        exit_immediately_on_rate_limit=exit_immediately,
                    )
                    latency = time.time() - start_time
                    if completion is not None:
                        out = (completion.choices[0].message.content or "").strip()
                        if len(out) > 40:
                            prompt_tokens = 0
                            completion_tokens = 0
                            try:
                                if hasattr(completion, "usage") and completion.usage:
                                    prompt_tokens = getattr(completion.usage, "prompt_tokens", 0) or 0
                                    completion_tokens = getattr(completion.usage, "completion_tokens", 0) or 0
                            except Exception:
                                pass

                            from .decision_ledger import log_llm_api_call
                            log_llm_api_call(
                                prompt_text=f"{system}\n{user}" if system else user,
                                model=model,
                                latency=latency,
                                response_text=out,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                api_url=endpoint,
                            )
                            return out
                        break

                    if err is not None and is_openrouter_rate_limit_error(err) and attempt == 0 and not exit_immediately:
                        time.sleep(rate_limit_sleep_seconds(err, 2.5))
                        continue
                    if err is not None:
                        logger.warning(
                            "[LLMClient] plain_text_generate provider=%s model=%s failed: %s",
                            prov_name,
                            model,
                            redact_secrets_in_text(str(err)),
                        )
                    break

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
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        Stream assistant text tokens (plain prose, not JSON). Used by TradeTalk chat.
        Yields incremental text chunks; transparently handles autonomous tool execution if provided.
        """
        # Phase A1: per-turn trajectory accumulator (only when caller wants traces).
        trajectory_acc = None
        if tool_trace_out is not None:
            from .chat_tool_telemetry import TrajectoryAccumulator

            trajectory_acc = TrajectoryAccumulator(
                trace_id=trace_id,
                session_id=session_id,
                message_id=message_id,
            )
        mt = max_tokens if max_tokens is not None else LLM_MAX_TOKENS
        _429_same_delay = float(os.environ.get("OPENROUTER_429_SAME_KEY_DELAY_SEC", "2.5"))
        _429_key_delay = float(os.environ.get("OPENROUTER_429_KEY_FAILOVER_DELAY_SEC", "1.0"))
        
        if self._nvidia_pool is None and self._openrouter_pool is None and not gemini_usable_for_chat():
            yield (
                "Chat requires a model provider: set NVIDIA_API_KEY, OPENROUTER_API_KEY, "
                "or GEMINI_API_KEY (with GEMINI_PRIMARY or GEMINI_LLM_FALLBACK)."
            )
            return

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
            gemini_error_only = False
            chat_phases: list[str] = []
            gemini_ok = gemini_usable_for_chat()

            if gemini_primary_enabled() and gemini_ok:
                chat_phases.append("gemini_chat")

            if self._nvidia_pool is not None:
                chat_phases.append("nvidia_chat")
            if self._openrouter_pool is not None:
                chat_phases.append("openrouter_chat")

            if gemini_ok and "gemini_chat" not in chat_phases:
                chat_phases.append("gemini_chat")

            if not chat_phases:
                yield (
                    "Chat requires a model provider: set NVIDIA_API_KEY, OPENROUTER_API_KEY, "
                    "or GEMINI_API_KEY (with GEMINI_PRIMARY or GEMINI_LLM_FALLBACK)."
                )
                return

            for phase in chat_phases:
                if stream_ok:
                    break
                
                if phase in ("nvidia_chat", "openrouter_chat"):
                    gemini_error_only = False
                    if phase == "nvidia_chat":
                        pool = self._nvidia_pool
                        phase_model = NVIDIA_MODEL_LIGHT
                        endpoint = NVIDIA_BASE_URL
                        prov_label = "NVIDIA"
                    else:
                        pool = self._openrouter_pool
                        phase_model = OPENROUTER_MODEL_LIGHT
                        endpoint = OPENROUTER_BASE_URL
                        prov_label = "OpenRouter"

                    if pool is None:
                        continue

                    async_clients = pool.async_clients_for_request(
                        should_try_other_openrouter_keys_on_429()
                    )
                    ci = 0
                    n_clients = len(async_clients)
                    abort_http_for_fallback = False
                    
                    # Check if there is any subsequent fallback in the cascade
                    current_phase_index = chat_phases.index(phase)
                    has_subsequent_fallback = len(chat_phases) > current_phase_index + 1

                    while ci < n_clients and not stream_ok and not abort_http_for_fallback:
                        async_client = async_clients[ci]
                        for attempt in range(2):
                            try:
                                kwargs = {
                                    "model": phase_model,
                                    "messages": msgs,
                                    "temperature": 0.35,
                                    "max_tokens": mt,
                                    "stream": True,
                                }
                                if tools:
                                    kwargs["tools"] = tools

                                accumulated_response = []
                                start_time = time.time()
                                async with self._sem_for_current_loop():
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
                                        accumulated_response.append(ch)
                                        yield ch

                                stream_ok = True
                                latency = time.time() - start_time
                                from .decision_ledger import log_llm_api_call
                                log_llm_api_call(
                                    prompt_text=str(msgs[-1].get("content") or ""),
                                    model=phase_model,
                                    latency=latency,
                                    response_text=f"Tool call: {tool_name}" if is_tool_call else "".join(accumulated_response),
                                    api_url=endpoint,
                                )
                                break
                            except Exception as e:
                                if not is_openrouter_rate_limit_error(e):
                                    logger.warning(
                                        "[LLMClient] stream_chat_plain %s model=%s failed: %s",
                                        prov_label,
                                        phase_model,
                                        redact_secrets_in_text(str(e)),
                                    )
                                    if has_subsequent_fallback:
                                        abort_http_for_fallback = True
                                        break
                                    yield f"\n\n[Chat error: {redact_secrets_in_text(str(e))[:200]}]"
                                    return

                                if gemini_instant_openrouter_failover() and has_subsequent_fallback:
                                    logger.info(
                                        "[LLMClient] %s LLM 429 — skipping backoff, immediate cascade failover",
                                        prov_label
                                    )
                                    abort_http_for_fallback = True
                                    break

                                wait = rate_limit_sleep_seconds(e, _429_same_delay)
                                if attempt == 0:
                                    logger.warning(
                                        "[LLMClient] %s rate limited (429) key=%s attempt=0, sleeping %.1fs then retry same key",
                                        prov_label,
                                        ci,
                                        wait,
                                    )
                                    await asyncio.sleep(wait)
                                    continue
                                if ci < n_clients - 1:
                                    extra = _429_key_delay
                                    logger.warning(
                                        "[LLMClient] %s rate limited (429) key=%s after retry, sleeping %.1fs then other key",
                                        prov_label,
                                        ci,
                                        extra,
                                    )
                                    await asyncio.sleep(extra)
                                    break

                                if has_subsequent_fallback:
                                    abort_http_for_fallback = True
                                    break

                                if prov_label == "OpenRouter":
                                    msg = (
                                        "[Chat error: OpenRouter rate limit (429) on all configured keys. "
                                        "Free models (e.g. `:free`) share strict upstream quotas — two API keys may both hit the same limit. "
                                        "Wait a few minutes, or set OPENROUTER_MODEL to a paid slug on openrouter.ai/models, "
                                        "or try another model.]"
                                    )
                                else:
                                    msg = f"[Chat error: {prov_label} API rate limit (429) on all configured keys. Wait and retry.]"
                                yield f"\n\n{msg}\n"
                                return
                        if abort_http_for_fallback:
                            break
                        if stream_ok:
                            break
                        ci += 1

                elif phase == "gemini_chat":
                    gemini_error_only = False
                    chat_model = GEMINI_MODEL
                    logger.info(
                        "[LLMClient] stream_chat_plain via Gemini model=%s",
                        chat_model,
                    )
                    try:
                        rest = msgs[1:] if len(msgs) > 1 else []
                        async with self._sem_for_current_loop():
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
                                        f"\n\n[Chat error (Gemini): "
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
                            "[LLMClient] Gemini stream failed: %s",
                            redact_secrets_in_text(str(e)),
                        )

            if not stream_ok and not gemini_error_only:
                yield (
                    "\n\n[Chat error: All configured model providers failed for this request. "
                    "Please check your API key configuration or try again later.]\n"
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

                _tool_t0 = time.perf_counter()
                _tool_error_type: Optional[str] = None
                try:
                    func = tool_handlers[tool_name]
                    result = await func(**args_dict) if asyncio.iscoroutinefunction(func) else func(**args_dict)
                except Exception as e:
                    result = f"Error executing {tool_name}: {e}"
                    _tool_error_type = type(e).__name__
                _tool_latency_ms = int((time.perf_counter() - _tool_t0) * 1000)
                if tool_trace_out is not None:
                    out = classify_tool_result(str(result))
                    if _tool_error_type is None and (
                        out == "error" or str(result).startswith("Error executing")
                    ):
                        _tool_error_type = "ToolHandlerError"
                    if trajectory_acc is None:  # defensive: should always be set above
                        from .chat_tool_telemetry import TrajectoryAccumulator

                        trajectory_acc = TrajectoryAccumulator(
                            trace_id=trace_id,
                            session_id=session_id,
                            message_id=message_id,
                        )
                    row = trajectory_acc.record(
                        tool_name=tool_name,
                        arguments=args_dict,
                        result=str(result),
                        outcome=out,
                        latency_ms=_tool_latency_ms,
                        error_type=_tool_error_type,
                    )
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

    async def generate_new_revenue_engine_score(self, ticker: str, context: dict) -> dict:
        """Risk-Return-Ratio Step — score new revenue engine components."""
        ctx = json.dumps(context, indent=2, default=str)
        if len(ctx) > 12000:
            ctx = ctx[:12000] + "\n…(truncated)"
        prompt = (
            f"Ticker: {ticker.upper()}\n\nContext JSON:\n{ctx}\n\n"
            "Score the new revenue engine factors based on the framework."
        )
        return await self.generate("new_revenue_engine_scorer", prompt)

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

    async def generate_daily_brief_batch(self, rows: List[dict]) -> List[dict]:
        """One LLM call refining verdicts for all daily-brief movers."""
        slim = []
        for r in rows:
            slim.append({
                "symbol": r.get("symbol"),
                "bucket": r.get("bucket"),
                "daily_return_pct": r.get("daily_return_pct"),
                "catalyst_status": r.get("catalyst_status"),
                "primary_cause_headline": (r.get("primary_cause_headline") or "")[:200],
                "scorecard_signal": r.get("scorecard_signal"),
                "scorecard_ratio": r.get("scorecard_ratio"),
                "valuation_pct_vs_fair": r.get("valuation_pct_vs_fair"),
                "heuristic_verdict": r.get("verdict"),
                "pe_ratio": r.get("pe_ratio"),
                "market_cap": r.get("market_cap"),
                "enrichment_source": r.get("enrichment_source"),
            })
        ctx = json.dumps({"rows": slim}, indent=2, default=str)
        if len(ctx) > 14000:
            ctx = ctx[:14000] + "\n…(truncated)"
        prompt = f"Daily movers context JSON:\n{ctx}\n\nReturn refined verdicts for every symbol."
        out = await self.generate("daily_brief_batch", prompt)
        if isinstance(out, dict) and isinstance(out.get("rows"), list):
            return out["rows"]
        return []

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
        async with self._sem_for_current_loop():
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
