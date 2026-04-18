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
import time
from datetime import datetime, timezone
from .schemas import DebateArgument, DebateResult, AgentStance
from .agent_policy_guardrails import ensure_capability, workload_scope
from .telemetry import get_tracer
from .tool_configs import get_tool_config
from .tool_handlers import decide_debate_bull_stance, decide_debate_bear_stance

logger = logging.getLogger(__name__)

_DEBATE_BULL_STANCE_DEFAULTS: dict = {
    "sir_bull_floor": 5.0,
    "rev_growth_bull_floor": 15.0,
    "r3m_bull_floor": 5.0,
    "sir_bear_ceiling": 2.0,
    "rev_growth_bear_ceiling": 0.0,
    "r3m_bear_ceiling": -10.0,
}

_DEBATE_BEAR_STANCE_DEFAULTS: dict = {
    "pe_bear_threshold": 50.0,
    "debt_eq_bear_threshold": 200.0,
    "r3m_bear_ceiling": -15.0,
    "pe_bull_ceiling": 20.0,
    "r3m_bull_floor": 0.0,
}

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
        cfg = get_tool_config("debate_stance_heuristic_bull", _DEBATE_BULL_STANCE_DEFAULTS)
        return AgentStance(decide_debate_bull_stance(data, cfg))

    if role == "bear":
        cfg = get_tool_config("debate_stance_heuristic_bear", _DEBATE_BEAR_STANCE_DEFAULTS)
        return AgentStance(decide_debate_bear_stance(data, cfg))

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
                     swarm_context: str = "",
                     *,
                     out_refs: list | None = None) -> DebateArgument:
    """Generic agent runner used by all 5 specialists.

    If ``out_refs`` is provided, each retrieval hit used to build the RAG
    context is appended as ``{chunk_id, collection, rank, distance, ticker,
    agent_role}`` so callers can record per-agent evidence into the Decision
    Ledger without widening the return type.
    """
    ensure_capability("debate", "knowledge_read")
    query_map = {
        "bull":     [
            "price_movements",
            "youtube_insights",
            "debate_history",
            "sp500_fundamentals_narratives",
            "stock_profiles",
            "earnings_memory",
        ],
        "bear":     [
            "macro_snapshots",
            "macro_alerts",
            "swarm_history",
            "stock_profiles",
            "earnings_memory",
        ],
        "macro":    ["macro_snapshots", "macro_alerts", "sp500_sector_analysis", "earnings_memory"],
        "value":    [
            "swarm_history",
            "debate_history",
            "sp500_fundamentals_narratives",
            "stock_profiles",
            "earnings_memory",
            "strategy_backtests",
        ],
        "momentum": ["price_movements", "youtube_insights", "stock_profiles", "earnings_memory", "strategy_backtests"],
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
        tw = {"ticker": ticker} if collection in (
            "debate_history",
            "price_movements",
            "swarm_history",
            "stock_profiles",
            "sp500_fundamentals_narratives",
        ) else None
        if out_refs is not None and hasattr(ks, "query_with_refs"):
            docs, refs = ks.query_with_refs(
                collection,
                f"{ticker} {role} investment analysis",
                n_results=2,
                where=tw,
            )
            for r in refs:
                try:
                    r["agent_role"] = role
                    out_refs.append(r)
                except Exception:
                    pass
        else:
            docs = ks.query(
                collection,
                f"{ticker} {role} investment analysis",
                n_results=2,
                where=tw,
            )
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
                         swarm_context: str = "",
                         *, out_refs: list | None = None) -> DebateArgument:
    live = {**debate_data, **macro_state}
    return await _run_agent(
        "bull", ticker, live, ks, llm, swarm_context=swarm_context, out_refs=out_refs
    )


async def run_bear_agent(ticker: str, debate_data: dict, macro_state: dict, ks, llm,
                         swarm_context: str = "",
                         *, out_refs: list | None = None) -> DebateArgument:
    live = {**debate_data, **macro_state}
    return await _run_agent(
        "bear", ticker, live, ks, llm, swarm_context=swarm_context, out_refs=out_refs
    )


async def run_macro_agent(ticker: str, macro_state: dict, ks, llm,
                          swarm_context: str = "",
                          *, out_refs: list | None = None) -> DebateArgument:
    return await _run_agent(
        "macro", ticker, macro_state, ks, llm, swarm_context=swarm_context, out_refs=out_refs
    )


async def run_value_agent(ticker: str, debate_data: dict, ks, llm,
                          swarm_context: str = "",
                          *, out_refs: list | None = None) -> DebateArgument:
    return await _run_agent(
        "value", ticker, debate_data, ks, llm, swarm_context=swarm_context, out_refs=out_refs
    )


async def run_momentum_agent(ticker: str, debate_data: dict, ks, llm,
                             swarm_context: str = "",
                             *, out_refs: list | None = None) -> DebateArgument:
    return await _run_agent(
        "momentum", ticker, debate_data, ks, llm, swarm_context=swarm_context, out_refs=out_refs
    )


VALID_VERDICTS = {"STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"}


async def run_moderator(
    ticker: str,
    arguments: list[DebateArgument],
    ks,
    llm,
    *,
    out_refs: list | None = None,
) -> tuple:
    """
    Synthesise 5 agent arguments into (verdict, confidence, summary, quality_warning).
    Validates the LLM verdict against an allowed enum. Retries once on failure.
    Returns: (verdict_str, confidence_float, summary_str, quality_warning_or_None)

    ``out_refs`` (optional): see :func:`_run_agent`. Moderator refs carry
    ``agent_role="moderator"``.
    """
    with workload_scope("debate", "knowledge_read"):
        if out_refs is not None and hasattr(ks, "query_with_refs"):
            context_docs, refs = ks.query_with_refs(
                "debate_history",
                f"{ticker} debate verdict",
                n_results=3,
                where={"ticker": ticker},
            )
            if not context_docs:
                context_docs, refs = ks.query_with_refs(
                    "debate_history", f"{ticker} debate verdict", n_results=3
                )
            for r in refs:
                try:
                    r["agent_role"] = "moderator"
                    out_refs.append(r)
                except Exception:
                    pass
        else:
            context_docs = ks.query(
                "debate_history",
                f"{ticker} debate verdict",
                n_results=3,
                where={"ticker": ticker},
            )
            if not context_docs:
                context_docs = ks.query(
                    "debate_history", f"{ticker} debate verdict", n_results=3
                )
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


def _store_agent_snapshot(ks, ticker: str, argument, macro_state: dict):
    """Store a compact snapshot of what each debate agent analyzed for future RAG."""
    col = ks._safe_col("debate_history")
    if not col:
        return
    kp = argument.key_points[:5] if argument.key_points else []
    points_text = "; ".join(kp) if kp else ""
    body = f"{argument.headline}. {points_text}" if points_text else argument.headline
    doc = (
        f"[{argument.agent_role}] {argument.stance.value} on {ticker}: "
        f"{body[:300]}"
    )
    entry_id = f"agent_{argument.agent_role}_{ticker}_{int(time.time())}"
    try:
        col.add(
            documents=[doc],
            metadatas=[{
                "ticker": ticker,
                "agent_role": argument.agent_role,
                "stance": argument.stance.value,
                "date": str(datetime.now(timezone.utc).date()),
                "market_regime": macro_state.get("market_regime", "unknown"),
            }],
            ids=[entry_id],
        )
    except Exception:
        pass


async def run_full_debate(ticker: str, debate_data: dict, macro_state: dict, ks, llm,
                          swarm_context: str = "") -> DebateResult:
    """
    Execute all 5 agents concurrently, then run the moderator.
    Returns a complete DebateResult.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("debate.run_full_debate"):
        return await _run_full_debate_impl(
            ticker, debate_data, macro_state, ks, llm, swarm_context=swarm_context,
        )


async def _run_full_debate_impl(ticker: str, debate_data: dict, macro_state: dict, ks, llm,
                                swarm_context: str = "") -> DebateResult:
    # Per-agent retrieval ref sinks (one per role to avoid concurrent mutation
    # of a shared list across asyncio tasks). Merged after gather.
    bull_refs: list = []
    bear_refs: list = []
    macro_refs: list = []
    value_refs: list = []
    momentum_refs: list = []
    moderator_refs: list = []

    bull_arg, bear_arg, macro_arg, value_arg, momentum_arg = await asyncio.gather(
        run_bull_agent(ticker, debate_data, macro_state, ks, llm,
                       swarm_context=swarm_context, out_refs=bull_refs),
        run_bear_agent(ticker, debate_data, macro_state, ks, llm,
                       swarm_context=swarm_context, out_refs=bear_refs),
        run_macro_agent(ticker, macro_state, ks, llm,
                        swarm_context=swarm_context, out_refs=macro_refs),
        run_value_agent(ticker, debate_data, ks, llm,
                        swarm_context=swarm_context, out_refs=value_refs),
        run_momentum_agent(ticker, debate_data, ks, llm,
                           swarm_context=swarm_context, out_refs=momentum_refs),
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

    verdict, confidence, summary, quality_warning = await run_moderator(
        ticker, arguments, ks, llm, out_refs=moderator_refs
    )

    if not verdict or verdict == "NEUTRAL" and heuristic_verdict != "NEUTRAL":
        verdict = heuristic_verdict

    # Store per-agent data snapshots for future learning
    try:
        for arg in arguments:
            _store_agent_snapshot(ks, ticker, arg, macro_state)
    except Exception as e:
        logger.warning(f"[Debate] agent snapshot storage failed: {e}")

    debate_result = DebateResult(
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

    # ── Decision-Outcome Ledger emission (Harness Engineering Phase 2) ──
    # Emits ONE row capturing the moderator-validated, override-applied,
    # user-facing debate verdict. The 5-agent arguments ride along in
    # output_json so the grader + correlation queries can split hits by
    # stance composition. Best-effort — failure must not affect callers.
    try:
        from . import decision_ledger as _dl
        regime = str(macro_state.get("market_regime", "") or "")
        features = [
            _dl.FeatureValue(name="market_regime", value_str=regime, regime=regime),
            _dl.FeatureValue(
                name="bull_score", value_num=float(bull_score), regime=regime,
            ),
            _dl.FeatureValue(
                name="bear_score", value_num=float(bear_score), regime=regime,
            ),
            _dl.FeatureValue(
                name="neutral_score", value_num=float(neutral_score), regime=regime,
            ),
        ]
        credit = macro_state.get("credit_stress_index")
        if credit is not None:
            features.append(
                _dl.FeatureValue(
                    name="credit_stress_index",
                    value_num=float(credit),
                    regime=regime,
                )
            )
        # Merge all per-agent refs → ledger evidence rows so a correlation
        # query can split hit-rate by (agent_role, collection, regime).
        all_refs: list[_dl.EvidenceRef] = []
        for bucket in (bull_refs, bear_refs, macro_refs, value_refs,
                       momentum_refs, moderator_refs):
            for r in bucket or []:
                try:
                    cid = str(r.get("chunk_id") or "")
                    if not cid:
                        continue
                    try:
                        rel = max(0.0, min(1.0, 1.0 - float(r.get("distance", 1.0))))
                    except Exception:
                        rel = None
                    all_refs.append(
                        _dl.EvidenceRef(
                            chunk_id=cid,
                            collection=str(r.get("collection") or ""),
                            rank=int(r.get("rank", 0)),
                            relevance=rel,
                        )
                    )
                except Exception:
                    continue

        _dl.emit_decision(
            decision_type="debate",
            symbol=ticker,
            horizon_hint="5d",  # debate verdicts are graded on a multi-day window
            verdict=verdict or "NEUTRAL",
            confidence=float(confidence),
            output={
                "ticker": ticker.upper(),
                "verdict": verdict,
                "confidence": confidence,
                "summary": summary,
                "bull_score": bull_score,
                "bear_score": bear_score,
                "neutral_score": neutral_score,
                "heuristic_verdict": heuristic_verdict,
                "quality_warning": quality_warning,
                "arguments": [a.model_dump() for a in arguments],
            },
            source_route="backend/debate_agents.py::_run_full_debate_impl",
            evidence=all_refs,
            features=features,
        )
    except Exception as e:
        logger.debug("[Debate] decision_ledger emit skipped: %s", e)

    return debate_result

