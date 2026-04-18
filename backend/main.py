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
    resource_registry,
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

from . import chat_session_store as chat_session_store_mod
chat_session_store_mod.init_chat_sessions_db()

from . import coral_hub as coral_hub_mod
coral_hub_mod.init_coral_hub_db()

from . import claim_store as claim_store_mod
claim_store_mod.init_claim_store_db()

# Decision-Outcome Ledger (Harness Engineering Phase 2).
# Creating the backend here kicks the migration so the decisions.db is ready
# before the first request hits. Installing the contract-validator sink wires
# `backend.contract_validator` violations into `contract_violations` so
# model-drift analytics can be answered with a SQL GROUP BY.
try:
    from . import decision_ledger as _decision_ledger
    _decision_ledger.get_ledger()
    _decision_ledger.install_contract_validator_sink()
except Exception as _e:  # never block startup over ledger init
    print(f"[DecisionLedger][startup] skipped (non-fatal): {_e}")

# RSPL resource registry (Phase A) — schema auto-applies in resource_registry's
# constructor; seeder reads backend/resources/prompts/*.yaml idempotently.
try:
    from . import resource_seeder as _resource_seeder
    _resource_seeder.seed_on_startup()
except Exception as _e:  # never block startup over prompt seeding
    print(f"[ResourceSeeder][startup] skipped (non-fatal): {_e}")

# ── Register routers ─────────────────────────────────────────────────────────
from .routers import (
    auth as auth_router,
    progress, challenges, portfolio, learning, academy,
    notifications, analysis, backtest, macro, knowledge, debug, chat,
    preferences, resources as resources_router,
    sepl as sepl_router,
    scorecard as scorecard_router,
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
app.include_router(resources_router.router)
app.include_router(sepl_router.router)
app.include_router(scorecard_router.router)

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

    # ── SEPL (Autogenesis §3.2) evolution cycle, feature-flagged off by default.
    # Even when SEPL_ENABLE=1, autocommit requires SEPL_AUTOCOMMIT=1; otherwise
    # the scheduled cycle runs DRY-RUN only and just logs what it WOULD have
    # done. See docs/SEPL.md for full gating semantics.
    from .sepl import sepl_enabled as _sepl_enabled
    if _sepl_enabled():
        from .sepl import (
            SEPL as _SEPL,
            SEPLKillSwitch as _SEPLKillSwitch,
            KnowledgeStoreReflectionSource as _KSR,
        )

        _sepl_scheduler = AsyncIOScheduler()

        async def _sepl_tick():
            try:
                autocommit = os.environ.get("SEPL_AUTOCOMMIT", "0").strip() == "1"
                reflection_source = _KSR(knowledge_store)
                sepl = _SEPL(
                    llm_client=llm_client,
                    registry=resource_registry,
                    reflection_source=reflection_source,
                )
                report = await sepl.run_cycle(dry_run=not autocommit)
                print(
                    f"[SEPL][tick] run={report.run_id} outcome={report.outcome.value} "
                    f"dry_run={report.dry_run} committed={report.committed_version}"
                )

                # Kill-switch pass: run AFTER the evolution tick so a freshly
                # committed change can't be rolled back on its own cycle. The
                # kill switch also uses ``dry_run=not autocommit`` — it will
                # never restore anything unless SEPL_AUTOCOMMIT=1.
                ks = _SEPLKillSwitch(
                    registry=resource_registry,
                    reflection_source=reflection_source,
                )
                for rpt in ks.check_all(dry_run=not autocommit):
                    print(
                        f"[SEPL][killswitch] target={rpt.target_name} outcome={rpt.outcome.value} "
                        f"delta={rpt.delta} post_n={rpt.post_commit_samples} restored={rpt.restored_to_version}"
                    )
            except Exception as e:
                print(f"[SEPL][tick] error (non-fatal): {e}")

        interval_hours = max(1, int(os.environ.get("SEPL_INTERVAL_HOURS", "24") or 24))
        _sepl_scheduler.add_job(
            _sepl_tick, "interval", hours=interval_hours, id="sepl_tick", max_instances=1
        )
        _sepl_scheduler.start()
        print(f"[SEPL] scheduler started (interval={interval_hours}h, autocommit={os.environ.get('SEPL_AUTOCOMMIT', '0')})")
    else:
        print("[SEPL] disabled (set SEPL_ENABLE=1 to activate evolution loop)")

    # ── SEPL-for-TOOLs (Phase C1 + C2, Autogenesis §3.2 for TOOL kind).
    # Independent scheduler parallel to the prompt loop above. Safety layers
    # stacked: master flag, dry-run default, min-margin, tier-aware daily cap
    # (tier-2+ hardcoded to 0), fixture-based offline scoring, kill switch
    # with its own autocommit gate. See docs/TOOL_EVOLUTION.md.
    from .sepl_tool import tool_sepl_enabled as _tool_sepl_enabled
    if _tool_sepl_enabled():
        from .sepl_tool import (
            SEPLTool as _SEPLTool,
            SEPLToolKillSwitch as _SEPLToolKillSwitch,
            tool_sepl_autocommit as _tool_sepl_autocommit,
            tool_sepl_dry_run as _tool_sepl_dry_run,
        )
        from .resource_registry import ResourceKind as _ResourceKind

        _sepl_tool_scheduler = AsyncIOScheduler()

        async def _sepl_tool_tick():
            try:
                # Discover the learnable tool names at tick time so new YAML
                # entries are picked up without a restart.
                learnable = [
                    r.name for r in resource_registry.list(_ResourceKind.TOOL)
                    if r.learnable
                ]
                if not learnable:
                    print("[SEPL-tool][tick] no learnable TOOL resources; skipping")
                    return

                dry = _tool_sepl_dry_run()
                sepl = _SEPLTool(registry=resource_registry)
                report = sepl.run_cycle(learnable)
                print(
                    f"[SEPL-tool][tick] run={report.run_id} target={report.tool_name} "
                    f"outcome={report.outcome.value} dry_run={report.dry_run} "
                    f"committed={report.committed_version}"
                )

                # Kill switch only restores when SEPL_TOOL_AUTOCOMMIT=1; absent
                # that it reports DRY_RUN outcomes only and never mutates.
                ks = _SEPLToolKillSwitch(registry=resource_registry)
                for rpt in ks.check_all(dry_run=not _tool_sepl_autocommit()):
                    print(
                        f"[SEPL-tool][killswitch] tool={rpt.tool_name} outcome={rpt.outcome.value} "
                        f"delta={rpt.delta} restored={rpt.restored_to_version}"
                    )
            except Exception as e:
                print(f"[SEPL-tool][tick] error (non-fatal): {e}")

        tool_interval_hours = max(
            1, int(os.environ.get("SEPL_TOOL_INTERVAL_HOURS", "24") or 24)
        )
        _sepl_tool_scheduler.add_job(
            _sepl_tool_tick, "interval",
            hours=tool_interval_hours, id="sepl_tool_tick", max_instances=1,
        )
        _sepl_tool_scheduler.start()
        print(
            f"[SEPL-tool] scheduler started (interval={tool_interval_hours}h, "
            f"dry_run={os.environ.get('SEPL_TOOL_DRY_RUN', '1')}, "
            f"autocommit={os.environ.get('SEPL_TOOL_AUTOCOMMIT', '0')})"
        )
    else:
        print("[SEPL-tool] disabled (set SEPL_TOOL_ENABLE=1 to activate tool evolution)")

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
