from fastapi import FastAPI, Query, BackgroundTasks, Depends
from typing import Optional as _Optional
from .auth import get_optional_user as _get_optional_user
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .schemas import (
    MarketState, FactorResult, MarketRegime, SwarmConsensus,
    MacroDataResponse, InvestorMetricsResponse, MacroAlert, AlertResponse,
    DebateResult, StrategyRules, BacktestResult,
)
from .agents import ShortInterestAgentPair, SocialSentimentAgentPair, MacroHealthAgentPair, PolymarketAgentPair, FundamentalHealthAgentPair
from .connectors import ShortsConnector, SocialSentimentConnector, MacroHealthConnector, PolymarketConnector, FundamentalsConnector, InvestorMetricsConnector, NewsScannerConnector
from .notification_agents import NotificationPipeline
from .knowledge_store import get_knowledge_store
from .llm_client import get_llm_client
from .agent_policy_guardrails import (
    PolicyBlockedError,
    ensure_capability,
    is_enabled as guardrails_enabled,
    redact_secrets_in_text,
    validate_startup_secrets,
)
import asyncio, json, time, os
from datetime import datetime as _dt

app = FastAPI(
    title="K2-Optimus Observer API",
    description="Observer trace API for the K2-Optimus Financial Swarm.",
    version="0.1.0"
)

# CORS — allow localhost dev + any Vercel preview/production subdomain + explicit extras
_cors_env = os.environ.get("CORS_ORIGINS", "")
_extra_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
_allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"] + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    # Wildcard regex covers all *.vercel.app subdomains so new Vercel deploys
    # don't require CORS_ORIGINS updates on Render.
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mock and Live data connectors representing the ingestion layer
shorts_connector = ShortsConnector()
social_connector = SocialSentimentConnector()
macro_connector = MacroHealthConnector()
poly_connector = PolymarketConnector()
fund_connector = FundamentalsConnector()
investor_metrics_connector = InvestorMetricsConnector()
news_scanner = NewsScannerConnector()
notification_pipeline = NotificationPipeline()
sse_clients: list = []

# Initialize persistent SQLite stores
from . import alert_store as db
db.init_db()

from .auth import init_users_db
init_users_db()

from . import user_progress as up
up.init_db()

from . import daily_challenge as dc
dc.init_challenges_db()

from . import paper_portfolio as pp
pp.init_portfolio_db()

from . import learning_path as lp
lp.init_learning_db()

from . import video_academy as va
va.init_academy_db()

# Register routers
from .routers import progress, challenges, portfolio, learning, academy
from .routers import auth as auth_router
app.include_router(auth_router.router)
app.include_router(progress.router)
app.include_router(challenges.router)
app.include_router(portfolio.router)
app.include_router(learning.router)
app.include_router(academy.router)

# Serve generated video files
import os as _os
_static_videos = _os.path.join(_os.path.dirname(__file__), "static", "videos")
_os.makedirs(_static_videos, exist_ok=True)
app.mount("/static/videos", StaticFiles(directory=_static_videos), name="videos")

# Initialize Knowledge Store and LLM Client (singletons)
knowledge_store = get_knowledge_store()
llm_client = get_llm_client()

# Cache for last trace data (populated by background loop)
last_trace_data: dict = {}

# ── Structured Observability Helper ──────────────────────────────────────────
_app_logger = logging.getLogger("tradetalk.tool")


def _log_tool_call(
    agent_name: str,
    tool_name: str,
    args: Any,
    result: Any,
    elapsed_ms: float,
) -> None:
    """Emit one structured JSON log line per tool call (Phase 4 observability)."""
    try:
        _app_logger.info(json.dumps({
            "ts":          _dt.utcnow().isoformat(),
            "agent":       agent_name,
            "tool":        tool_name,
            "args_size":   len(str(args)),
            "output_size": len(str(result)),
            "latency_ms":  round(elapsed_ms, 1),
        }))
    except Exception:
        pass  # logging must never crash the app

def _sync_scan_and_process():
    """Run the entire scan+process cycle synchronously (called from thread)."""
    import requests  # noqa — ensure requests is available in thread
    data = news_scanner._sync_fetch()
    new_headlines = data.get("new_headlines", [])
    trace = notification_pipeline.process_with_trace(new_headlines)
    trace["stored_alerts"] = db.get_all_alerts(limit=10)
    if new_headlines:
        for alert in trace.get("alerts", []):
            db.insert_alert(alert)
    return trace, new_headlines

async def news_scan_loop():
    global last_trace_data
    await asyncio.sleep(5)
    while True:
        try:
            trace, new_headlines = await asyncio.get_event_loop().run_in_executor(None, _sync_scan_and_process)
            last_trace_data = trace
            if new_headlines:
                for alert in trace.get("alerts", []):
                    event_data = json.dumps(alert)
                    for queue in sse_clients:
                        await queue.put(event_data)
                    # Knowledge hook — persist each alert for RAG
                    try:
                        ensure_capability("notifications", "knowledge_write")
                        knowledge_store.add_macro_alert(MacroAlert(**alert))
                    except Exception:
                        pass
                print(f"[K2-Notifier] {trace['alerts_produced']} new alerts saved to DB")
            else:
                print("[K2-Notifier] No new macro headlines")
        except Exception as e:
            print(f"[K2-Notifier] Error: {e}")
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    issues = validate_startup_secrets()
    if issues:
        for issue in issues:
            print(f"[PolicyGuardrail][startup] {issue}")
        if os.environ.get("GUARDRAILS_STRICT_STARTUP", "0").strip() == "1":
            raise RuntimeError("Agent policy guardrails startup validation failed. See logs.")

    asyncio.create_task(news_scan_loop())
    # Start daily knowledge pipeline scheduler
    from .daily_pipeline import start_scheduler
    start_scheduler(knowledge_store, llm_client=llm_client)
    # Keep HF Space alive — pings self every 5 min, re-ingests S&P 500 hourly
    from .keep_alive import start_keep_alive
    start_keep_alive()
    # Kick off S&P 500 fundamentals ingestion in background at startup
    async def _run_sp500_on_startup():
        try:
            from .sp500_ingestion_pipeline import run_sp500_ingestion
            await asyncio.sleep(15)  # let the server fully init first
            await run_sp500_ingestion()
        except Exception as e:
            print(f"[SP500Pipeline][startup] ingestion error: {e}")
    asyncio.create_task(_run_sp500_on_startup())

@app.get("/notifications/stream")
async def notification_stream():
    queue = asyncio.Queue()
    sse_clients.append(queue)
    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': time.time()})}\n\n"
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_clients.remove(queue)
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

@app.get("/notifications/history", response_model=AlertResponse)
async def get_notification_history():
    """Returns all unseen alerts from persistent store."""
    alerts = db.get_all_alerts(limit=30)
    unread = db.count_unread()
    return AlertResponse(alerts=[MacroAlert(**a) for a in alerts], total=len(alerts), unread=unread)

@app.post("/notifications/dismiss/{alert_id}")
async def dismiss_notification(alert_id: str):
    """Mark a single alert as seen."""
    db.mark_seen(alert_id)
    return {"status": "dismissed"}

@app.post("/notifications/mark-seen")
async def mark_all_seen():
    """Mark all alerts as seen (user opened the bell). Seen alerts are then deleted."""
    db.mark_all_seen()
    db.delete_seen()
    return {"status": "all_seen_and_cleared", "remaining": db.count_unread()}

@app.post("/notifications/scan")
async def manual_scan():
    data = await news_scanner.fetch_data()
    alerts = notification_pipeline.process(data.get("new_headlines", []))
    for alert in alerts:
        db.insert_alert(alert)
        for queue in sse_clients:
            await queue.put(json.dumps(alert))
    return {"scanned": data["total_scanned"], "new_alerts": len(alerts)}

@app.get("/notifications/trace")
async def notification_trace():
    """Return cached trace data from last background scan (instant, no network)."""
    if last_trace_data:
        # Refresh stored_alerts to show current DB state
        last_trace_data["stored_alerts"] = db.get_all_alerts(limit=10)
        return last_trace_data
    # If no scan happened yet, return minimal data
    return {
        "total_scanned": 0, "passed_filter": 0, "rejected": 0, "alerts_produced": 0,
        "headlines": [], "stored_alerts": db.get_all_alerts(limit=10), "alerts": [],
    }

@app.get("/macro", response_model=MacroDataResponse)
async def get_macro_data():
    """
    K2-Optimus Phase 9: Dedicated Global Macro Analysis Endpoint.
    """
    data = await macro_connector.fetch_data()
    ind = data["indicators"]
    return MacroDataResponse(
        vix_level=ind["vix_level"],
        credit_stress_index=ind["credit_stress_index"],
        market_regime="BULL_NORMAL" if ind["credit_stress_index"] <= 1.1 else "BEAR_STRESS",
        sectors=data["sectors"],
        consumer_spending=data["consumer_spending"],
        capital_flows=data["capital_flows"],
        cash_reserves=data["cash_reserves"],
        usd_broad_index=ind.get("usd_broad_index"),
        usd_index_change_5d_pct=ind.get("usd_index_change_5d_pct"),
        usd_strength_label=ind.get("usd_strength_label") or "unknown",
        dxy_level=ind.get("dxy_level"),
        dxy_change_5d_pct=ind.get("dxy_change_5d_pct"),
        dxy_strength_label=ind.get("dxy_strength_label") or "unknown",
        treasury_2y=ind.get("treasury_2y"),
        treasury_10y=ind.get("treasury_10y"),
        yield_curve_spread_10y_2y=ind.get("yield_curve_spread_10y_2y"),
        fed_funds_rate=ind.get("fed_funds_rate"),
        cpi_yoy=ind.get("cpi_yoy"),
        unemployment_rate=ind.get("unemployment"),
        macro_narrative=ind.get("macro_narrative") or "",
        fred_fetched_at=ind.get("fred_fetched_at"),
    )

@app.get("/metrics/{ticker}", response_model=InvestorMetricsResponse)
async def get_investor_metrics(ticker: str):
    """
    Fetches live and proxy fundamental metrics used by elite value/distressed investors.
    """
    data = await investor_metrics_connector.fetch_data(ticker=ticker)
    
    if "error" in data:
        return InvestorMetricsResponse(ticker=ticker.upper(), metrics={})
        
    return InvestorMetricsResponse(
        ticker=ticker.upper(),
        metrics=data["metrics"]
    )

@app.get("/trace", response_model=SwarmConsensus)
async def get_agent_trace(
    ticker: str = Query("GME", description="The stock ticker to analyze."),
    credit_stress: float = Query(None, description="Optional override for Credit stress index."),
    _auth_user=Depends(_get_optional_user),
):
    """
    K2-Optimus Phase 6: Live Swarm execution across Short Interest, Social, and Macro dimensions.
    """
    import asyncio
    
    # 1. Evaluate context using live Macro Health (VIX)
    macro_data = await macro_connector.fetch_data()
    live_credit_stress = macro_data["indicators"]["credit_stress_index"]
    
    # Allow override for testing purposes via the UI, otherwise use live
    actual_stress = credit_stress if credit_stress is not None else live_credit_stress
    
    regime = MarketRegime.BULL_NORMAL if actual_stress <= 1.1 else MarketRegime.BEAR_STRESS
    
    market_state = MarketState(
        credit_stress_index=actual_stress,
        market_regime=regime
    )
    
    # 2. Instantiate AgentPairs with knowledge_store for reflection-aware analysis
    short_pair = ShortInterestAgentPair(connector=shorts_connector,
                                        knowledge_store=knowledge_store, llm_client=llm_client)
    social_pair = SocialSentimentAgentPair(connector=social_connector,
                                           knowledge_store=knowledge_store, llm_client=llm_client)
    poly_pair = PolymarketAgentPair(connector=poly_connector,
                                     knowledge_store=knowledge_store, llm_client=llm_client)
    fund_pair = FundamentalHealthAgentPair(connector=fund_connector,
                                           knowledge_store=knowledge_store, llm_client=llm_client)
    
    # 3. Execute Swarm concurrently
    results = await asyncio.gather(
        short_pair.run(market_state=market_state, ticker=ticker),
        social_pair.run(market_state=market_state, ticker=ticker),
        poly_pair.run(market_state=market_state, ticker=ticker),
        fund_pair.run(market_state=market_state, ticker=ticker)
    )
    
    short_res, social_res, poly_res, fund_res = results

    # 4. Confidence-weighted consensus
    verified = [r for r in results if r.status == "VERIFIED"]
    rejected = [r for r in results if r.status == "REJECTED"]

    if verified:
        total_conf = sum(r.confidence for r in verified)
        weighted_signal = sum(r.confidence * r.trading_signal for r in verified) / total_conf
    else:
        weighted_signal = 0.0
        total_conf = 0.0

    if rejected:
        global_verdict = "REJECTED (MACRO/RISK STRESS)"
        global_signal = 0
    elif weighted_signal > 0.7:
        global_verdict = "STRONG BUY"
        global_signal = 1
    elif weighted_signal > 0.4:
        global_verdict = "BUY"
        global_signal = 1
    elif weighted_signal < -0.7:
        global_verdict = "STRONG SELL"
        global_signal = -1
    elif weighted_signal < -0.4:
        global_verdict = "SELL"
        global_signal = -1
    else:
        global_verdict = "NEUTRAL"
        global_signal = 0

    avg_confidence = sum(r.confidence for r in results) / len(results)

    # 5. LLM conflict synthesis when factors disagree
    consensus_rationale = ""
    signals = [r.trading_signal for r in verified]
    has_conflict = len(set(signals)) > 1 and len(verified) >= 2
    if has_conflict:
        try:
            factor_dicts = [r.model_dump() for r in results]
            synthesis = await llm_client.generate_swarm_synthesis(ticker, factor_dicts)
            consensus_rationale = synthesis.get("consensus_rationale", "")
            synth_verdict = synthesis.get("verdict", "").upper()
            synth_confidence = float(synthesis.get("confidence", avg_confidence))
            if synth_verdict in ("STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"):
                global_verdict = synth_verdict
                avg_confidence = synth_confidence
        except Exception as e:
            consensus_rationale = f"Synthesis unavailable: {e}"

    # XP hook — award for running a valuation (only when logged in)
    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "valuation", note=ticker)
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
            "fundamentals": fund_res
        }
    )

    # Knowledge hook — persist this analysis for future RAG queries
    try:
        knowledge_store.add_swarm_analysis(consensus)
    except Exception as e:
        print(f"[KnowledgeHook] add_swarm_analysis failed: {e}")

    return consensus


# ── AI Debate Endpoint ────────────────────────────────────────────────────────

@app.get("/debate", response_model=DebateResult)
async def debate_ticker(ticker: str = Query("GME", description="Stock ticker to debate."),
                        _auth_user=Depends(_get_optional_user)):
    """
    Run a full 5-agent AI investment debate on a ticker.
    All agents use RAG from ChromaDB for historical context.
    """
    from .connectors.debate_data import fetch_debate_data
    from .debate_agents import run_full_debate

    # Fetch live market data for the ticker
    debate_data = await fetch_debate_data(ticker)

    # Build macro state for agents
    macro_data = await macro_connector.fetch_data()
    ind = macro_data["indicators"]
    macro_state = {
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

    result = await run_full_debate(ticker, debate_data, macro_state, knowledge_store, llm_client)

    # Knowledge hook — save debate result
    try:
        ensure_capability("debate", "knowledge_write")
        knowledge_store.add_debate(result)
    except Exception as e:
        print(f"[KnowledgeHook] add_debate failed: {redact_secrets_in_text(str(e))}")

    # XP hook
    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "debate", note=ticker)
        except Exception:
            pass

    return result


# ── Sequential Analyze Endpoint (Swarm then Debate) ──────────────────────────

class AnalyzeResponse(BaseModel):
    swarm: SwarmConsensus
    debate: DebateResult


@app.get("/analyze", response_model=AnalyzeResponse)
async def analyze_ticker(
    ticker: str = Query("GME", description="Stock ticker for deep analysis."),
    credit_stress: float = Query(None, description="Optional override for credit stress index."),
    _auth_user=Depends(_get_optional_user),
):
    """
    Sequential pipeline: run Swarm first, then feed SwarmConsensus into the
    Debate as grounding context so LLM agents reason over quantitative data.
    """
    import asyncio

    # Phase 1 — Swarm
    swarm_result = await get_agent_trace(ticker=ticker, credit_stress=credit_stress, _auth_user=_auth_user)

    # Build swarm context string for debate agents
    factor_summary = "; ".join(
        f"{name}: signal={fr.trading_signal}, conf={fr.confidence:.2f}"
        for name, fr in swarm_result.factors.items()
    )
    swarm_context = (
        f"[Swarm pre-analysis for {ticker.upper()}] "
        f"Verdict: {swarm_result.global_verdict}, confidence: {swarm_result.confidence:.2f}. "
        f"Factors: {factor_summary}. "
        f"{swarm_result.consensus_rationale}"
    )

    # Phase 2 — Debate with swarm context injected
    from .connectors.debate_data import fetch_debate_data
    from .debate_agents import run_full_debate

    debate_data = await fetch_debate_data(ticker)
    macro_data = await macro_connector.fetch_data()
    ind = macro_data["indicators"]
    macro_state = {
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

    debate_result = await run_full_debate(
        ticker, debate_data, macro_state, knowledge_store, llm_client,
        swarm_context=swarm_context,
    )

    try:
        ensure_capability("debate", "knowledge_write")
        knowledge_store.add_debate(debate_result)
    except Exception:
        pass

    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "deep_analysis", note=ticker)
        except Exception:
            pass

    return AnalyzeResponse(swarm=swarm_result, debate=debate_result)


# ── Strategy Backtest Endpoint ────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy: str
    start_date: str = "2020-01-01"
    end_date: str = "2024-01-01"


@app.post("/backtest", response_model=BacktestResult)
async def run_backtest_endpoint(req: BacktestRequest, _auth_user=Depends(_get_optional_user)):
    """
    Parse a plain-English investing strategy, run a backtest, and return results.
    Uses the configured LLM backend to parse strategy and explain results.
    """
    from .strategy_parser import parse_strategy
    from .backtest_engine import run_backtest

    # Parse strategy text into rules
    rules = await parse_strategy(req.strategy, req.start_date, req.end_date, llm_client, knowledge_store)

    # Run backtest simulation
    result = await run_backtest(rules, llm_client, knowledge_store)
    print(
        f"[BacktestRAG] retrieved_docs_count={result.retrieval_telemetry.retrieved_docs_count} "
        f"reflection_hits={result.retrieval_telemetry.reflection_hits}"
    )

    # Knowledge hook — save backtest result
    try:
        ensure_capability("backtest", "knowledge_write")
        knowledge_store.add_backtest(result)
        knowledge_store.add_reflection(result)
    except Exception as e:
        print(f"[KnowledgeHook] add_backtest failed: {redact_secrets_in_text(str(e))}")

    # XP hook
    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "backtest", note=req.strategy[:40])
        except Exception:
            pass

    return result


# ── Knowledge Endpoints ───────────────────────────────────────────────────────

@app.get("/knowledge/stats")
async def knowledge_stats():
    """Returns entry counts per ChromaDB collection and pipeline status."""
    return knowledge_store.stats()


@app.get("/knowledge/export")
async def export_knowledge():
    """Download all debate + backtest history as a JSONL fine-tuning file."""
    jsonl_content = knowledge_store.export_jsonl()
    return Response(
        content=jsonl_content,
        media_type="application/jsonl",
        headers={"Content-Disposition": "attachment; filename=tradetalk_training_data.jsonl"},
    )


@app.get("/knowledge/pipeline-status")
async def pipeline_status():
    """Returns status of the last daily knowledge pipeline run."""
    stats = knowledge_store.stats()
    return {
        "pipeline_status": stats.get("pipeline_status", {}),
        "collection_sizes": stats.get("collections", {}),
    }


@app.get("/knowledge/reflections")
async def knowledge_reflections(n: int = 20):
    """Debug endpoint to inspect recently stored reflection memories."""
    n = max(1, min(n, 100))
    reflections = knowledge_store.get_recent_reflections(n=n)
    return {"reflections": reflections, "total": len(reflections)}


@app.post("/knowledge/pipeline-run")
async def trigger_pipeline():
    """Manually trigger the daily knowledge pipeline (for testing)."""
    from .daily_pipeline import run_daily_pipeline
    summary = await run_daily_pipeline(knowledge_store)
    return {"status": "complete", "summary": summary}


@app.post("/knowledge/sp500-ingest")
async def trigger_sp500_ingestion(tickers: list[str] = None):
    """
    Manually trigger the S&P 500 fundamentals + sector ingestion pipeline.
    Optionally pass a list of tickers to limit ingestion scope (default: PRIORITY_TICKERS).
    """
    from .sp500_ingestion_pipeline import run_sp500_ingestion
    summary = await run_sp500_ingestion(tickers=tickers)
    return {"status": "complete", "summary": summary}


@app.get("/knowledge/sp500-stats")
async def sp500_ingestion_stats():
    """Returns counts for the S&P 500 vector collections."""
    stats = knowledge_store.stats()
    collections = stats.get("collections", {})
    return {
        "sp500_fundamentals_narratives": collections.get("sp500_fundamentals_narratives", 0),
        "sp500_sector_analysis":         collections.get("sp500_sector_analysis", 0),
        "vector_backend":                stats.get("vector_backend", "unknown"),
    }


@app.get("/strategies/leaderboard")
async def strategy_leaderboard(n: int = 20):
    """
    Return top N backtested strategies from the knowledge base, sorted by CAGR.
    Each entry includes key performance stats so the frontend can display a ranked list.
    """
    entries = knowledge_store.get_strategy_leaderboard(n=n)
    return {"strategies": entries, "total": len(entries)}


@app.get("/llm/status")
async def llm_status():
    """Show which LLM backend, model tiers, and routing all agents use."""
    from .llm_client import RAG_TOP_K_DEFAULT, MODEL_TIER, OPENROUTER_MODEL_LIGHT, _model_for_role
    backend = llm_client.backend
    ks_stats = knowledge_store.stats()
    role_models = {role: _model_for_role(role) for role in MODEL_TIER}
    return {
        "backend": backend,
        "provider": getattr(llm_client, "provider", backend),
        "model_heavy": llm_client.model if backend == "openrouter" else "rule-based",
        "model_light": OPENROUTER_MODEL_LIGHT if backend == "openrouter" else "rule-based",
        "endpoint": llm_client.endpoint if backend == "openrouter" else None,
        "guardrails_enabled": guardrails_enabled(),
        "vector_backend": ks_stats.get("vector_backend", "chroma"),
        "rag_top_k_default": RAG_TOP_K_DEFAULT,
        "role_model_mapping": role_models,
        "note": "Roles use heavy or light model tier based on reasoning complexity.",
    }


@app.get("/runtime/policy-check")
async def runtime_policy_check():
    """Agent policy guardrails self-test: verifies capability blocking and secret validation."""
    issues = validate_startup_secrets()
    blocked = False
    blocked_reason = ""
    try:
        ensure_capability("debate", "notifications_emit")
    except PolicyBlockedError as e:
        blocked = True
        blocked_reason = str(e)
    return {
        "guardrails_enabled": guardrails_enabled(),
        "policy_block_check": "pass" if blocked else "fail",
        "policy_block_reason": blocked_reason,
        "startup_secret_issues": issues,
    }


# ── Cache & Router Debug Endpoints ───────────────────────────────────────────

@app.get("/cache/stats")
async def cache_stats_endpoint():
    """Returns L1 in-memory tool-call cache hit/miss stats."""
    from .cache import cache_stats
    return cache_stats()


@app.delete("/cache/flush")
async def cache_flush_endpoint(tool_name: str = None):
    """Flush the L1 tool cache (optionally filter by tool name)."""
    from .cache import invalidate
    removed = invalidate(tool_name or None)
    return {"flushed": removed, "tool_name": tool_name}


@app.get("/query/route")
async def query_route_endpoint(q: str = Query(..., description="User query to classify")):
    """
    Debug endpoint: classify a query into sql / rag / python / general using
    the lightweight regex-based router (no LLM call, instant).
    """
    from .query_router import route_query_detail
    return route_query_detail(q)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
