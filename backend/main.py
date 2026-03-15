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
import asyncio, json, time, os

app = FastAPI(
    title="K2-Optimus Observer API",
    description="Observer trace API for the K2-Optimus Financial Swarm.",
    version="0.1.0"
)

# Allow origins from CORS_ORIGINS env var (comma-separated) plus localhost fallback
_cors_env = os.environ.get("CORS_ORIGINS", "")
_extra_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
_allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"] + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
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
    asyncio.create_task(news_scan_loop())
    # Start daily knowledge pipeline scheduler
    from .daily_pipeline import start_scheduler
    start_scheduler(knowledge_store)

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
    return MacroDataResponse(
        vix_level=data["indicators"]["vix_level"],
        credit_stress_index=data["indicators"]["credit_stress_index"],
        market_regime="BULL_NORMAL" if data["indicators"]["credit_stress_index"] <= 1.1 else "BEAR_STRESS",
        sectors=data["sectors"],
        consumer_spending=data["consumer_spending"],
        capital_flows=data["capital_flows"],
        cash_reserves=data["cash_reserves"]
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
    
    # 2. Instantiate 3 AgentPairs (Macro exists via context only now)
    short_pair = ShortInterestAgentPair(connector=shorts_connector)
    social_pair = SocialSentimentAgentPair(connector=social_connector)
    poly_pair = PolymarketAgentPair(connector=poly_connector)
    fund_pair = FundamentalHealthAgentPair(connector=fund_connector)
    
    # 3. Execute Swarm concurrently
    results = await asyncio.gather(
        short_pair.run(market_state=market_state, ticker=ticker),
        social_pair.run(market_state=market_state, ticker=ticker),
        poly_pair.run(market_state=market_state, ticker=ticker),
        fund_pair.run(market_state=market_state, ticker=ticker)
    )
    
    short_res, social_res, poly_res, fund_res = results
    
    # 4. Aggregated Swarm Logic (Basic Consensus mechanism)
    bull_votes = sum(1 for r in results if r.status == "VERIFIED" and r.trading_signal == 1)
    reject_votes = sum(1 for r in results if r.status == "REJECTED")
    
    global_verdict = "NEUTRAL"
    if reject_votes > 0:
        global_verdict = "REJECTED (MACRO/RISK STRESS)"
    elif bull_votes >= 3:
        global_verdict = "STRONG BUY / SQUEEZE ALIGNMENT"
        
    avg_confidence = sum(r.confidence for r in results) / len(results)

    # XP hook — award for running a valuation (only when logged in)
    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "valuation", note=ticker)
        except Exception:
            pass

    consensus = SwarmConsensus(
        ticker=ticker.upper(),
        macro_state=market_state,
        global_signal=bull_votes,
        global_verdict=global_verdict,
        confidence=avg_confidence,
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
    macro_state = {
        "credit_stress_index": macro_data["indicators"]["credit_stress_index"],
        "vix_level":           macro_data["indicators"]["vix_level"],
        "market_regime":       "BULL_NORMAL" if macro_data["indicators"]["credit_stress_index"] <= 1.1 else "BEAR_STRESS",
    }

    result = await run_full_debate(ticker, debate_data, macro_state, knowledge_store, llm_client)

    # Knowledge hook — save debate result
    try:
        knowledge_store.add_debate(result)
    except Exception as e:
        print(f"[KnowledgeHook] add_debate failed: {e}")

    # XP hook
    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "debate", note=ticker)
        except Exception:
            pass

    return result


# ── Strategy Backtest Endpoint ────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy: str
    start_date: str = "2020-01-01"
    end_date: str = "2024-01-01"


@app.post("/backtest", response_model=BacktestResult)
async def run_backtest_endpoint(req: BacktestRequest, _auth_user=Depends(_get_optional_user)):
    """
    Parse a plain-English investing strategy, run a backtest, and return results.
    Uses Gemini to parse strategy and explain results.
    """
    from .strategy_parser import parse_strategy
    from .backtest_engine import run_backtest

    # Parse strategy text into rules
    rules = await parse_strategy(req.strategy, req.start_date, req.end_date, llm_client, knowledge_store)

    # Run backtest simulation
    result = await run_backtest(rules, llm_client, knowledge_store)

    # Knowledge hook — save backtest result
    try:
        knowledge_store.add_backtest(result)
    except Exception as e:
        print(f"[KnowledgeHook] add_backtest failed: {e}")

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


@app.post("/knowledge/pipeline-run")
async def trigger_pipeline():
    """Manually trigger the daily knowledge pipeline (for testing)."""
    from .daily_pipeline import run_daily_pipeline
    summary = await run_daily_pipeline(knowledge_store)
    return {"status": "complete", "summary": summary}


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
    """Show which LLM backend and model all agents are currently using."""
    from .llm_client import OLLAMA_BASE_URL, OLLAMA_MODEL, GEMINI_MODEL
    backend = llm_client.backend
    return {
        "backend": backend,
        "model": OLLAMA_MODEL if backend == "ollama" else (GEMINI_MODEL if backend == "gemini" else "rule-based"),
        "endpoint": OLLAMA_BASE_URL if backend == "ollama" else ("https://generativelanguage.googleapis.com" if backend == "gemini" else None),
        "agents_using_this_model": [
            "bull", "bear", "macro", "value", "momentum",
            "moderator", "strategy_parser", "backtest_explainer"
        ],
        "note": "All 8 agent roles share one LLMClient singleton — changing the backend affects every agent.",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
