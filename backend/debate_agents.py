"""
Debate Agents — 5 specialist LLM-powered investment agents + Moderator.
Each agent:
  1. Fetches live data from connectors
  2. Queries multiple ChromaDB collections for historical context (RAG)
  3. Calls LLMClient.generate_argument(role, ticker, live_data, context)
  4. Returns a DebateArgument

Moderator synthesises all 5 arguments into a final DebateResult.
"""
import asyncio
import logging
from .schemas import DebateArgument, DebateResult, AgentStance
from .agent_policy_guardrails import ensure_capability, workload_scope

logger = logging.getLogger(__name__)

# ── Agent metadata ────────────────────────────────────────────────────────────
AGENT_META = {
    "bull":     {"icon": "TrendingUp",   "color": "#10b981"},
    "bear":     {"icon": "ShieldAlert",  "color": "#ef4444"},
    "macro":    {"icon": "Globe",        "color": "#3b82f6"},
    "value":    {"icon": "Scale",        "color": "#f59e0b"},
    "momentum": {"icon": "Zap",          "color": "#8b5cf6"},
}


def _determine_stance(role: str, data: dict, llm_result: dict) -> AgentStance:
    """Heuristic stance from live data, validated against LLM result."""
    # LLM may include stance in its output
    raw = str(llm_result.get("stance", "")).upper()
    if "BULL" in raw:
        return AgentStance.BULLISH
    if "BEAR" in raw:
        return AgentStance.BEARISH
    if "NEUTRAL" in raw:
        return AgentStance.NEUTRAL

    # Data-driven fallback per agent
    if role == "bull":
        sir = data.get("short_interest_ratio", 0)
        rev = data.get("revenue_growth", 0)
        r3m = data.get("price_return_3m", 0)
        if sir > 5 or rev > 15 or r3m > 5:
            return AgentStance.BULLISH
        if sir < 2 and rev < 0 and r3m < -10:
            return AgentStance.BEARISH
        return AgentStance.NEUTRAL

    if role == "bear":
        debt_eq = data.get("debt_to_equity") or 0
        pe = data.get("pe_ratio") or 0
        r3m = data.get("price_return_3m", 0)
        if (pe and pe > 50) or (debt_eq and debt_eq > 200) or r3m < -15:
            return AgentStance.BEARISH
        if pe and pe < 20 and r3m > 0:
            return AgentStance.BULLISH
        return AgentStance.NEUTRAL

    if role == "macro":
        # placeholder — macro agent uses macro state from data
        csi = data.get("credit_stress_index", 1.0)
        if csi > 1.5:
            return AgentStance.BEARISH
        if csi < 0.9:
            return AgentStance.BULLISH
        return AgentStance.NEUTRAL

    if role == "value":
        roe = data.get("roe", 0)
        pe  = data.get("pe_ratio") or 0
        if roe > 15 and pe and pe < 25:
            return AgentStance.BULLISH
        if roe < 0 or (pe and pe > 50):
            return AgentStance.BEARISH
        return AgentStance.NEUTRAL

    if role == "momentum":
        pct_52wk = data.get("pct_of_52wk_high", 0.5)
        r3m = data.get("price_return_3m", 0)
        if pct_52wk > 0.75 and r3m > 5:
            return AgentStance.BULLISH
        if pct_52wk < 0.40 and r3m < -10:
            return AgentStance.BEARISH
        return AgentStance.NEUTRAL

    return AgentStance.NEUTRAL


async def _run_agent(role: str, ticker: str, live_data: dict, ks, llm,
                     swarm_context: str = "") -> DebateArgument:
    """Generic agent runner used by all 5 specialists."""
    ensure_capability("debate", "knowledge_read")
    query_map = {
        "bull":     ["price_movements", "youtube_insights", "debate_history"],
        "bear":     ["macro_snapshots", "macro_alerts", "swarm_history"],
        "macro":    ["macro_snapshots", "macro_alerts"],
        "value":    ["swarm_history", "debate_history"],
        "momentum": ["price_movements", "youtube_insights"],
    }

    context_docs = []

    if swarm_context:
        context_docs.append(swarm_context)

    market_regime = str(live_data.get("market_regime", "")).lower()
    reflection_filters = {}
    if market_regime:
        if "bear" in market_regime:
            reflection_filters["market_regime"] = "risk_off"
        else:
            reflection_filters["market_regime"] = "risk_on_or_mixed"

    if hasattr(ks, "query_reflections"):
        reflection_docs, _, telemetry = ks.query_reflections(
            query_text=f"{ticker} {role} setup",
            n_results=2,
            filters=reflection_filters or None,
        )
        context_docs.extend(reflection_docs)
        logger.info(
            f"[DebateRAG] role={role} ticker={ticker} docs={telemetry.get('retrieved_docs_count', 0)} "
            f"reflection_hits={telemetry.get('reflection_hits', 0)}"
        )

    for collection in query_map.get(role, ["debate_history"]):
        docs = ks.query(collection, f"{ticker} {role} investment analysis", n_results=2)
        context_docs.extend(docs)
    context = ks.format_context(context_docs)

    with workload_scope("debate", "llm_inference"):
        result = await llm.generate_argument(role, ticker, live_data, context)

    # Extract fields safely
    headline    = result.get("headline", f"{role.capitalize()} perspective on {ticker}")
    key_points  = result.get("key_points", [f"Analysis based on current market data for {ticker}."])
    confidence  = float(result.get("confidence", 0.6))
    stance      = _determine_stance(role, live_data, result)

    meta = AGENT_META[role]
    return DebateArgument(
        agent_role=role,
        agent_icon=meta["icon"],
        stance=stance,
        headline=headline,
        key_points=key_points if isinstance(key_points, list) else [str(key_points)],
        supporting_data={k: v for k, v in live_data.items() if k in (
            "current_price", "price_return_1m", "price_return_3m",
            "short_interest_ratio", "pe_ratio", "roe", "revenue_growth",
            "debt_to_equity", "pct_of_52wk_high", "beta",
        )},
        confidence=min(max(confidence, 0.0), 1.0),
    )


async def run_bull_agent(ticker: str, debate_data: dict, macro_state: dict, ks, llm,
                         swarm_context: str = "") -> DebateArgument:
    live = {**debate_data, **macro_state}
    return await _run_agent("bull", ticker, live, ks, llm, swarm_context=swarm_context)


async def run_bear_agent(ticker: str, debate_data: dict, macro_state: dict, ks, llm,
                         swarm_context: str = "") -> DebateArgument:
    live = {**debate_data, **macro_state}
    return await _run_agent("bear", ticker, live, ks, llm, swarm_context=swarm_context)


async def run_macro_agent(ticker: str, macro_state: dict, ks, llm,
                          swarm_context: str = "") -> DebateArgument:
    return await _run_agent("macro", ticker, macro_state, ks, llm, swarm_context=swarm_context)


async def run_value_agent(ticker: str, debate_data: dict, ks, llm,
                          swarm_context: str = "") -> DebateArgument:
    return await _run_agent("value", ticker, debate_data, ks, llm, swarm_context=swarm_context)


async def run_momentum_agent(ticker: str, debate_data: dict, ks, llm,
                             swarm_context: str = "") -> DebateArgument:
    return await _run_agent("momentum", ticker, debate_data, ks, llm, swarm_context=swarm_context)


VALID_VERDICTS = {"STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"}


async def run_moderator(ticker: str, arguments: list[DebateArgument], ks, llm) -> tuple:
    """
    Synthesise 5 agent arguments into (verdict, confidence, summary, quality_warning).
    Validates the LLM verdict against an allowed enum. Retries once on failure.
    Returns: (verdict_str, confidence_float, summary_str, quality_warning_or_None)
    """
    with workload_scope("debate", "knowledge_read"):
        context_docs = ks.query("debate_history", f"{ticker} debate verdict", n_results=3)
    if hasattr(ks, "query_reflections"):
        reflection_docs, _, telemetry = ks.query_reflections(
            query_text=f"{ticker} final verdict",
            n_results=2,
            filters=None,
        )
        context_docs.extend(reflection_docs)
        logger.info(
            f"[DebateRAG] role=moderator ticker={ticker} docs={telemetry.get('retrieved_docs_count', 0)} "
            f"reflection_hits={telemetry.get('reflection_hits', 0)}"
        )
    context = ks.format_context(context_docs)

    args_dicts = [a.model_dump() for a in arguments]
    avg_confidence = sum(a.confidence for a in arguments) / len(arguments) if arguments else 0.5

    quality_warning = None
    max_attempts = 2
    for attempt in range(max_attempts):
        with workload_scope("debate", "llm_inference"):
            result = await llm.generate_moderator_verdict(ticker, args_dicts, context)

        verdict = result.get("verdict", "").upper().strip()
        summary = result.get("summary", "Mixed signals across specialist agents.")
        confidence = float(result.get("confidence", avg_confidence))

        if verdict in VALID_VERDICTS and confidence >= 0.3:
            return verdict, round(confidence, 3), summary, quality_warning

        if attempt == 0:
            logger.warning(
                "[Moderator] Invalid verdict '%s' (conf=%.2f) on attempt %d, retrying...",
                verdict, confidence, attempt + 1,
            )

    # All attempts exhausted — fall back to heuristic
    quality_warning = f"LLM moderator returned invalid verdict '{verdict}'; using heuristic."
    logger.warning("[Moderator] %s", quality_warning)
    return "NEUTRAL", round(avg_confidence, 3), summary, quality_warning


def _score_arguments(arguments: list[DebateArgument]) -> tuple[int, int, int]:
    """Count bull / bear / neutral stance votes."""
    bull = sum(1 for a in arguments if a.stance == AgentStance.BULLISH)
    bear = sum(1 for a in arguments if a.stance == AgentStance.BEARISH)
    neut = sum(1 for a in arguments if a.stance == AgentStance.NEUTRAL)
    return bull, bear, neut


async def run_full_debate(ticker: str, debate_data: dict, macro_state: dict, ks, llm,
                          swarm_context: str = "") -> DebateResult:
    """
    Execute all 5 agents concurrently, then run the moderator.
    Returns a complete DebateResult.
    """
    bull_arg, bear_arg, macro_arg, value_arg, momentum_arg = await asyncio.gather(
        run_bull_agent(ticker, debate_data, macro_state, ks, llm, swarm_context=swarm_context),
        run_bear_agent(ticker, debate_data, macro_state, ks, llm, swarm_context=swarm_context),
        run_macro_agent(ticker, macro_state, ks, llm, swarm_context=swarm_context),
        run_value_agent(ticker, debate_data, ks, llm, swarm_context=swarm_context),
        run_momentum_agent(ticker, debate_data, ks, llm, swarm_context=swarm_context),
    )

    arguments = [bull_arg, bear_arg, macro_arg, value_arg, momentum_arg]
    bull_score, bear_score, neutral_score = _score_arguments(arguments)

    if bull_score >= 4:
        heuristic_verdict = "STRONG BUY"
    elif bull_score == 3:
        heuristic_verdict = "BUY"
    elif bear_score >= 4:
        heuristic_verdict = "STRONG SELL"
    elif bear_score == 3:
        heuristic_verdict = "SELL"
    else:
        heuristic_verdict = "NEUTRAL"

    verdict, confidence, summary, quality_warning = await run_moderator(ticker, arguments, ks, llm)

    if not verdict or verdict == "NEUTRAL" and heuristic_verdict != "NEUTRAL":
        verdict = heuristic_verdict

    return DebateResult(
        ticker=ticker.upper(),
        arguments=arguments,
        verdict=verdict,
        consensus_confidence=confidence,
        moderator_summary=summary,
        bull_score=bull_score,
        bear_score=bear_score,
        neutral_score=neutral_score,
        quality_warning=quality_warning,
    )
