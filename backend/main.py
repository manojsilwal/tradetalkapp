"""
TradeTalk App API — application entry point.

All route handlers are in backend/routers/. Shared state lives in backend/deps.py.
"""
import asyncio
import json
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .telemetry import RequestIDMiddleware
from .agent_policy_guardrails import (
    ensure_capability, validate_startup_secrets,
)
from .schemas import MacroAlert
from .deps import (
    sse_clients, last_trace_data,
    news_scanner, notification_pipeline,
    knowledge_store, llm_client, db, up,
)

app = FastAPI(
    title="TradeTalk App API",
    description="AI-powered investment analysis API for the TradeTalk platform.",
    version="0.1.0",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
_cors_env = os.environ.get("CORS_ORIGINS", "")
_extra_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
_allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"] + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)

# ── Initialize persistent SQLite stores ──────────────────────────────────────
db.init_db()

from .auth import init_users_db
init_users_db()

up.init_db()

from . import daily_challenge as dc
dc.init_challenges_db()

from . import paper_portfolio as pp
pp.init_portfolio_db()

from . import learning_path as lp
lp.init_learning_db()

from . import video_academy as va
va.init_academy_db()

from . import user_preferences as uprefs
uprefs.init_preferences_db()

from . import agent_memory as agent_memory_mod
agent_memory_mod.init_agent_memory_db()

# ── Register routers ─────────────────────────────────────────────────────────
from .routers import (
    auth as auth_router,
    progress, challenges, portfolio, learning, academy,
    notifications, analysis, backtest, macro, knowledge, debug, chat,
    preferences,
)

app.include_router(auth_router.router)
app.include_router(progress.router)
app.include_router(challenges.router)
app.include_router(portfolio.router)
app.include_router(learning.router)
app.include_router(academy.router)
app.include_router(notifications.router)
app.include_router(analysis.router)
app.include_router(backtest.router)
app.include_router(macro.router)
app.include_router(knowledge.router)
app.include_router(debug.router)
app.include_router(chat.router)
app.include_router(preferences.router)

# ── Serve generated video files ──────────────────────────────────────────────
_static_videos = os.path.join(os.path.dirname(__file__), "static", "videos")
os.makedirs(_static_videos, exist_ok=True)
app.mount("/static/videos", StaticFiles(directory=_static_videos), name="videos")


# ── Background tasks ─────────────────────────────────────────────────────────

def _sync_scan_and_process():
    """Run the entire scan+process cycle synchronously (called from thread)."""
    import requests  # noqa
    data = news_scanner._sync_fetch()
    new_headlines = data.get("new_headlines", [])
    trace = notification_pipeline.process_with_trace(new_headlines)
    trace["stored_alerts"] = db.get_all_alerts(limit=10)
    if new_headlines:
        for alert in trace.get("alerts", []):
            db.insert_alert(alert)
    return trace, new_headlines


async def news_scan_loop():
    await asyncio.sleep(5)
    while True:
        try:
            trace, new_headlines = await asyncio.get_event_loop().run_in_executor(None, _sync_scan_and_process)
            last_trace_data.clear()
            last_trace_data.update(trace)
            if new_headlines:
                for alert in trace.get("alerts", []):
                    event_data = json.dumps(alert)
                    for queue in sse_clients:
                        await queue.put(event_data)
                    try:
                        ensure_capability("notifications", "knowledge_write")
                        knowledge_store.add_macro_alert(MacroAlert(**alert))
                    except Exception:
                        pass
                print(f"[TradeTalk-Notifier] {trace['alerts_produced']} new alerts saved to DB")
            else:
                print("[TradeTalk-Notifier] No new macro headlines")
        except Exception as e:
            print(f"[TradeTalk-Notifier] Error: {e}")
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
    from .daily_pipeline import start_scheduler
    start_scheduler(knowledge_store, llm_client=llm_client)

    # Register market intel refresh jobs on APScheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    _mil_scheduler = AsyncIOScheduler()
    from .market_intel import refresh_fast as _mil_fast, refresh_slow as _mil_slow
    _mil_scheduler.add_job(_mil_fast, "interval", minutes=10, id="mil_fast", max_instances=1)
    _mil_scheduler.add_job(_mil_slow, "interval", minutes=30, id="mil_slow", max_instances=1)
    _mil_scheduler.start()
    print("[MarketIntel] APScheduler: fast=10min, slow=30min")

    from .keep_alive import start_keep_alive
    start_keep_alive()

    def _sp500_ingest_on_startup() -> bool:
        v = os.environ.get("SP500_INGEST_ON_STARTUP", "").strip().lower()
        if v in ("1", "true", "yes"):
            return True
        if v in ("0", "false", "no"):
            return False
        render = os.environ.get("RENDER", "").strip().lower()
        if render in ("true", "1", "yes"):
            return False
        return True

    async def _run_sp500_on_startup():
        if not _sp500_ingest_on_startup():
            print("[SP500Pipeline][startup] skipped (set SP500_INGEST_ON_STARTUP=1 to enable on this host)")
            return
        try:
            from .sp500_ingestion_pipeline import run_sp500_ingestion
            await asyncio.sleep(15)
            await run_sp500_ingestion()
        except Exception as e:
            print(f"[SP500Pipeline][startup] ingestion error: {e}")

    asyncio.create_task(_run_sp500_on_startup())

    async def _warm_market_l1():
        await asyncio.sleep(3)
        try:
            from .market_l1_cache import refresh

            await refresh()
        except Exception as e:
            print(f"[MarketL1] startup warm failed: {e}")

    asyncio.create_task(_warm_market_l1())

    async def _warm_market_intel():
        await asyncio.sleep(10)  # stagger after L1 warm
        try:
            from .market_intel import refresh_fast, refresh_slow
            print("[MarketIntel] Starting startup warm (S&P 500 batch + news + slow layer)...")
            await asyncio.gather(refresh_fast(), refresh_slow(), return_exceptions=True)
            print("[MarketIntel] Startup warm complete.")
        except Exception as e:
            print(f"[MarketIntel] startup warm failed: {e}")

    asyncio.create_task(_warm_market_intel())



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
