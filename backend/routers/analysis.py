"""Analysis endpoints — swarm trace, AI debate, deep analyze."""
import asyncio
import time as _time
from datetime import datetime as _dt2, timezone as _tz2
from typing import Optional

from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel

from ..schemas import (
    MarketState, MarketRegime, SwarmConsensus, DebateResult,
    DecisionTerminalPayload,
)
from ..agents import (
    ShortInterestAgentPair, SocialSentimentAgentPair,
    PolymarketAgentPair, FundamentalHealthAgentPair,
)
from ..auth import get_optional_user
from ..ingress_models import (
    AnalyzeIngressRequest, DebateIngressRequest, TraceIngressRequest,
    validate_ticker_query,
)
from ..agent_policy_guardrails import ensure_capability, redact_secrets_in_text
from ..telemetry import get_tracer
from ..rate_limiter import rate_limit
from ..deps import (
    shorts_connector, social_connector, poly_connector, fund_connector,
    knowledge_store, llm_client, tool_registry, up,
)
from ..coral_agents import hub_record_attempt
from .. import user_preferences as uprefs

router = APIRouter(tags=["analysis"])

_rl_expensive = rate_limit("expensive")


def _peer_summaries_from_factor_results(results: list) -> dict[str, str]:
    keys = ["short_interest", "social_sentiment", "polymarket", "fundamentals"]
    out: dict[str, str] = {}
    for k, r in zip(keys, results):
        out[k] = (getattr(r, "rationale", None) or "")[:500]
    return out


def _format_peer_highlights(peer: dict[str, str]) -> str:
    return "\n".join(f"- **{k}**: {(v or '')[:220]}" for k, v in peer.items())


def _store_factor_snapshot(ks, ticker: str, factor_name: str, result, market_state):
    """Store individual factor analysis for granular future RAG."""
    col = ks._safe_col("swarm_history")
    if not col:
        return
    rationale_preview = (getattr(result, "rationale", None) or "")[:400]
    doc = (
        f"[{factor_name}] {ticker} analysis: status={result.status}, "
        f"signal={result.trading_signal}, confidence={result.confidence:.2f}. "
        f"Summary: {rationale_preview}"
    )
    entry_id = f"factor_{factor_name}_{ticker}_{int(_time.time())}"
    try:
        col.add(
            documents=[doc],
            metadatas=[{
                "ticker": ticker,
                "factor": factor_name,
                "signal": result.trading_signal,
                "confidence": result.confidence,
                "status": result.status,
                "market_regime": market_state.market_regime.value if hasattr(market_state.market_regime, 'value') else str(market_state.market_regime),
                "date": str(_dt2.now(_tz2.utc).date()),
            }],
            ids=[entry_id],
        )
    except Exception:
        pass


def _macro_state_from_indicators(ind: dict) -> dict:
    """Shared macro blob for debate agents."""
    return {
        "credit_stress_index": ind["credit_stress_index"],
        "vix_level":           ind["vix_level"],
        "market_regime":       "BULL_NORMAL" if ind["credit_stress_index"] <= 1.1 else "BEAR_STRESS",
        "macro_narrative":     ind.get("macro_narrative") or "",
        "usd_strength_label":  ind.get("usd_strength_label") or "unknown",
        "usd_broad_index":     ind.get("usd_broad_index"),
        "usd_index_change_5d_pct": ind.get("usd_index_change_5d_pct"),
        "dxy_level":           ind.get("dxy_level"),
        "dxy_change_5d_pct":   ind.get("dxy_change_5d_pct"),
        "dxy_strength_label":  ind.get("dxy_strength_label") or "unknown",
        "yield_curve_spread_10y_2y": ind.get("yield_curve_spread_10y_2y"),
        "treasury_10y":        ind.get("treasury_10y"),
        "treasury_2y":         ind.get("treasury_2y"),
    }


async def _execute_swarm_trace(
    ticker: str,
    credit_stress: Optional[float],
    _auth_user,
) -> SwarmConsensus:
    tracer = get_tracer()
    with tracer.start_as_current_span("swarm.trace"):
        try:
            macro_data = await tool_registry.invoke("macro_fetch", {}, timeout_s=45.0)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail={"error": "timeout", "tool": "macro_fetch", "message": "Macro data fetch timed out"},
            ) from None

        live_credit_stress = macro_data["indicators"]["credit_stress_index"]
        actual_stress = credit_stress if credit_stress is not None else live_credit_stress
        regime = MarketRegime.BULL_NORMAL if actual_stress <= 1.1 else MarketRegime.BEAR_STRESS
        market_state = MarketState(credit_stress_index=actual_stress, market_regime=regime)

        short_pair = ShortInterestAgentPair(connector=shorts_connector, knowledge_store=knowledge_store, llm_client=llm_client)
        social_pair = SocialSentimentAgentPair(connector=social_connector, knowledge_store=knowledge_store, llm_client=llm_client)
        poly_pair = PolymarketAgentPair(connector=poly_connector, knowledge_store=knowledge_store, llm_client=llm_client)
        fund_pair = FundamentalHealthAgentPair(connector=fund_connector, knowledge_store=knowledge_store, llm_client=llm_client)

        results = await asyncio.gather(
            short_pair.run(market_state=market_state, ticker=ticker),
            social_pair.run(market_state=market_state, ticker=ticker),
            poly_pair.run(market_state=market_state, ticker=ticker),
            fund_pair.run(market_state=market_state, ticker=ticker),
        )

        short_res, social_res, poly_res, fund_res = results
        peer_summaries = _peer_summaries_from_factor_results(results)
        verified = [r for r in results if r.status == "VERIFIED"]
        rejected = [r for r in results if r.status == "REJECTED"]

        if verified:
            total_conf = sum(r.confidence for r in verified)
            weighted_signal = sum(r.confidence * r.trading_signal for r in verified) / total_conf
        else:
            weighted_signal = 0.0
            total_conf = 0.0

        if rejected:
            global_verdict, global_signal = "REJECTED (MACRO/RISK STRESS)", 0
        elif weighted_signal > 0.7:
            global_verdict, global_signal = "STRONG BUY", 1
        elif weighted_signal > 0.4:
            global_verdict, global_signal = "BUY", 1
        elif weighted_signal < -0.7:
            global_verdict, global_signal = "STRONG SELL", -1
        elif weighted_signal < -0.4:
            global_verdict, global_signal = "SELL", -1
        else:
            global_verdict, global_signal = "NEUTRAL", 0

        avg_confidence = sum(r.confidence for r in results) / len(results)

        consensus_rationale = ""
        signals = [r.trading_signal for r in verified]
        has_conflict = len(set(signals)) > 1 and len(verified) >= 2
        if has_conflict:
            try:
                factor_dicts = [r.model_dump() for r in results]
                synthesis = await llm_client.generate_swarm_synthesis(
                    ticker, factor_dicts, peer_summaries=peer_summaries,
                )
                consensus_rationale = synthesis.get("consensus_rationale", "")
                synth_verdict = synthesis.get("verdict", "").upper()
                synth_confidence = float(synthesis.get("confidence", avg_confidence))
                if synth_verdict in ("STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"):
                    global_verdict = synth_verdict
                    avg_confidence = synth_confidence
            except Exception as e:
                consensus_rationale = f"Synthesis unavailable: {e}"

        highlights = _format_peer_highlights(peer_summaries)
        if highlights.strip():
            peer_section = f"## Peer factor highlights\n{highlights}"
            if consensus_rationale:
                consensus_rationale = f"{consensus_rationale}\n\n{peer_section}"
            else:
                consensus_rationale = peer_section

        if _auth_user:
            try:
                up.award_xp(_auth_user.id, "valuation", note=ticker)
                uprefs.learn_from_action(_auth_user.id, "trace", {"ticker": ticker})
            except Exception:
                pass

        consensus = SwarmConsensus(
            ticker=ticker.upper(),
            macro_state=market_state,
            global_signal=global_signal,
            global_verdict=global_verdict,
            confidence=avg_confidence,
            consensus_rationale=consensus_rationale,
            factors={
                "short_interest": short_res,
                "social_sentiment": social_res,
                "polymarket": poly_res,
                "fundamentals": fund_res,
            },
        )

        try:
            knowledge_store.add_swarm_analysis(consensus)
        except Exception as e:
            print(f"[KnowledgeHook] add_swarm_analysis failed: {e}")

        try:
            hub_record_attempt(
                f"trace_{ticker.upper()}",
                "swarm_trace",
                float(global_signal),
                float(avg_confidence),
            )
        except Exception:
            pass

        try:
            from ..coral_dreaming import EVENT_SWARM
            from ..coral_hub import log_handoff_event

            log_handoff_event(
                EVENT_SWARM,
                {
                    "ticker": ticker.upper(),
                    "global_signal": int(global_signal),
                    "global_verdict": consensus.global_verdict,
                    "confidence": float(avg_confidence),
                    "rationale_excerpt": (consensus.consensus_rationale or "")[:800],
                },
            )
        except Exception:
            pass

        # Store per-factor snapshots for granular RAG
        try:
            for factor_name, factor_result in consensus.factors.items():
                _store_factor_snapshot(knowledge_store, ticker, factor_name, factor_result, market_state)
        except Exception as e:
            print(f"[KnowledgeHook] factor snapshot failed: {e}")

        return consensus


async def _execute_debate(
    ticker: str,
    _auth_user,
    swarm_context: str = "",
    *,
    award_debate_xp: bool = True,
) -> DebateResult:
    from ..debate_agents import run_full_debate

    try:
        debate_data = await tool_registry.invoke("fetch_debate_data", {"ticker": ticker}, timeout_s=90.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail={"error": "timeout", "tool": "fetch_debate_data", "message": "Debate market data fetch timed out"}) from None

    try:
        macro_data = await tool_registry.invoke("macro_fetch", {}, timeout_s=45.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail={"error": "timeout", "tool": "macro_fetch", "message": "Macro data fetch timed out"}) from None

    ind = macro_data["indicators"]
    macro_state = _macro_state_from_indicators(ind)

    result = await run_full_debate(ticker, debate_data, macro_state, knowledge_store, llm_client, swarm_context=swarm_context)

    try:
        ensure_capability("debate", "knowledge_write")
        knowledge_store.add_debate(result)
    except Exception as e:
        print(f"[KnowledgeHook] add_debate failed: {redact_secrets_in_text(str(e))}")

    if _auth_user and award_debate_xp:
        try:
            up.award_xp(_auth_user.id, "debate", note=ticker)
            uprefs.learn_from_action(_auth_user.id, "debate", {"ticker": ticker})
        except Exception:
            pass

    try:
        from ..coral_dreaming import EVENT_DEBATE
        from ..coral_hub import log_handoff_event

        log_handoff_event(
            EVENT_DEBATE,
            {
                "ticker": result.ticker.upper(),
                "verdict": result.verdict,
                "consensus_confidence": float(result.consensus_confidence),
                "moderator_excerpt": (result.moderator_summary or "")[:800],
            },
        )
    except Exception:
        pass

    return result


class AnalyzeResponse(BaseModel):
    swarm: SwarmConsensus
    debate: DebateResult


async def _execute_analyze(
    ticker: str,
    credit_stress: Optional[float],
    _auth_user,
    *,
    award_deep_analysis_xp: bool = True,
) -> AnalyzeResponse:
    swarm_result = await _execute_swarm_trace(ticker, credit_stress, _auth_user)
    factor_summary = "; ".join(f"{name}: signal={fr.trading_signal}, conf={fr.confidence:.2f}" for name, fr in swarm_result.factors.items())
    swarm_context = (
        f"[Swarm pre-analysis for {ticker.upper()}] "
        f"Verdict: {swarm_result.global_verdict}, confidence: {swarm_result.confidence:.2f}. "
        f"Factors: {factor_summary}. {swarm_result.consensus_rationale}"
    )
    debate_result = await _execute_debate(ticker, _auth_user, swarm_context=swarm_context, award_debate_xp=False)
    if _auth_user and award_deep_analysis_xp:
        try:
            up.award_xp(_auth_user.id, "deep_analysis", note=ticker)
        except Exception:
            pass
    return AnalyzeResponse(swarm=swarm_result, debate=debate_result)


@router.get("/trace", response_model=SwarmConsensus, dependencies=[Depends(_rl_expensive)])
async def get_agent_trace(
    ticker: str = Query("GME", description="The stock ticker to analyze."),
    credit_stress: float = Query(None, description="Optional override for Credit stress index."),
    _auth_user=Depends(get_optional_user),
):
    """Live Swarm execution across Short Interest, Social, and Macro dimensions."""
    t = validate_ticker_query(ticker)
    return await _execute_swarm_trace(t, credit_stress, _auth_user)


@router.post("/trace", response_model=SwarmConsensus, dependencies=[Depends(_rl_expensive)])
async def post_agent_trace(body: TraceIngressRequest, _auth_user=Depends(get_optional_user)):
    """Schema-first swarm trace."""
    return await _execute_swarm_trace(body.ticker, body.credit_stress, _auth_user)


@router.get("/debate", response_model=DebateResult, dependencies=[Depends(_rl_expensive)])
async def debate_ticker_get(
    ticker: str = Query("GME", description="Stock ticker to debate."),
    _auth_user=Depends(get_optional_user),
):
    """Run a full 5-agent AI investment debate on a ticker."""
    t = validate_ticker_query(ticker)
    return await _execute_debate(t, _auth_user)


@router.post("/debate", response_model=DebateResult, dependencies=[Depends(_rl_expensive)])
async def debate_ticker_post(body: DebateIngressRequest, _auth_user=Depends(get_optional_user)):
    """Schema-first debate."""
    return await _execute_debate(body.ticker, _auth_user)


@router.get("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(_rl_expensive)])
async def analyze_ticker_get(
    ticker: str = Query("GME", description="Stock ticker for deep analysis."),
    credit_stress: float = Query(None, description="Optional override for credit stress index."),
    _auth_user=Depends(get_optional_user),
):
    """Sequential pipeline: Swarm + Debate with grounding context."""
    t = validate_ticker_query(ticker)
    return await _execute_analyze(t, credit_stress, _auth_user)


@router.post("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(_rl_expensive)])
async def analyze_ticker_post(body: AnalyzeIngressRequest, _auth_user=Depends(get_optional_user)):
    """Schema-first deep analysis."""
    return await _execute_analyze(body.ticker, body.credit_stress, _auth_user)


@router.get("/decision-terminal", response_model=DecisionTerminalPayload, dependencies=[Depends(_rl_expensive)])
async def decision_terminal_get(
    ticker: str = Query("AAPL", description="Stock ticker for the decision terminal."),
    credit_stress: float = Query(None, description="Optional credit stress override (same as /analyze)."),
    _auth_user=Depends(get_optional_user),
):
    """
    Glanceable four-panel terminal: valuation heuristics, quality snapshot, fused verdict/sentiment,
    and 3Y scenario prices. Runs full swarm + debate once. Does not award deep_analysis XP.
    """
    from ..decision_terminal import run_decision_terminal_request

    t = validate_ticker_query(ticker)
    return await run_decision_terminal_request(
        t,
        credit_stress,
        _auth_user,
        execute_analyze=_execute_analyze,
        tool_registry=tool_registry,
        poly_connector=poly_connector,
        llm_client=llm_client,
    )


@router.post("/decision-terminal", response_model=DecisionTerminalPayload, dependencies=[Depends(_rl_expensive)])
async def decision_terminal_post(body: AnalyzeIngressRequest, _auth_user=Depends(get_optional_user)):
    """Schema-first decision terminal (same behavior as GET)."""
    from ..decision_terminal import run_decision_terminal_request

    t = validate_ticker_query(body.ticker)
    return await run_decision_terminal_request(
        t,
        body.credit_stress,
        _auth_user,
        execute_analyze=_execute_analyze,
        tool_registry=tool_registry,
        poly_connector=poly_connector,
        llm_client=llm_client,
    )
