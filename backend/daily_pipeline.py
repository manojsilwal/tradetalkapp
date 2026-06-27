"""
Daily Knowledge Pipeline — orchestrates knowledge ingestion jobs.
Runs every night at midnight via APScheduler.
Each run adds new records to the vector store; historical data remains searchable.

Set DATA_LAKE_DAILY_INCREMENTAL=0 to skip Phase-7 Parquet append / event slices (long-running).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from .agent_policy_guardrails import ensure_capability

logger = logging.getLogger(__name__)


async def run_daily_pipeline(knowledge_store, llm_client=None) -> dict:
    """
    Run all ingestion jobs concurrently, then run swarm outcome tracking.
    Returns a summary dict logged and stored in knowledge_store.pipeline_status.
    """
    logger.info("[DailyPipeline] Starting daily knowledge ingestion...")
    ensure_capability("scheduler", "knowledge_write")
    today = str(datetime.now(timezone.utc).date())
    summary = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "price_movements_added": 0,
        "youtube_videos_added": 0,
        "macro_snapshot_added": False,
        "swarm_outcomes_tracked": 0,
        "errors": [],
    }

    from .sec_filing_job import run_sec_filing_job

    jobs = [
        _ingest_price_movements(knowledge_store),
        _ingest_macro_snapshot(knowledge_store),
        _ingest_youtube(knowledge_store),
        run_sec_filing_job(),
    ]
    job_names = ["price_movements", "macro_snapshot", "youtube", "sec_filing_job"]
    if os.environ.get("DATA_LAKE_DAILY_INCREMENTAL", "1").strip().lower() not in ("0", "false", "no"):
        jobs.append(asyncio.to_thread(_run_data_lake_incremental_sync))
        job_names.append("data_lake_incremental")

    results = await asyncio.gather(*jobs, return_exceptions=True)

    for i, result in enumerate(results):
        job_name = job_names[i]
        if isinstance(result, Exception):
            summary["errors"].append(f"{job_name}: {result}")
            logger.warning(f"[DailyPipeline] {job_name} failed: {result}")
        elif isinstance(result, dict):
            summary.update(result)

    # Outcome grader runs on the dedicated 02:10 UTC scheduler job (see below).
    # Keeping it out of the midnight ingest pass avoids grading the same decisions twice.

    knowledge_store.update_pipeline_status(**summary)

    # RAG Bridge: index new BQ rows into vector store for agent retrieval
    try:
        from .mcp_server.rag_bridge import run_full_index
        rag_results = run_full_index(days_back=1)
        summary["rag_bridge_indexed"] = rag_results
    except Exception as e:
        logger.debug("[DailyPipeline] RAG bridge skipped: %s", e)

    logger.info(f"[DailyPipeline] Complete — {summary}")
    return summary


async def _ingest_price_movements(knowledge_store) -> dict:
    """Fetch top S&P 500 movers and store in price_movements collection."""
    ensure_capability("scheduler", "market_data_read")
    from .connectors.price_movements import fetch_top_movers
    movers = await fetch_top_movers()
    count = 0
    for mover in movers:
        knowledge_store.add_price_movement(
            ticker=mover["ticker"],
            change_pct=mover["change_pct"],
            volume_ratio=mover["volume_ratio"],
            sector=mover["sector"],
            context=mover.get("context", ""),
        )
        count += 1
        
    try:
        from .ingestion_agent import emit_ingestion_candidate
        symbols_list = [m["ticker"] for m in movers if m.get("ticker")]
        # Await because we are running in an async pipeline step
        await emit_ingestion_candidate(
            source_type="daily_brief",
            symbols=symbols_list,
            triggered_by="scheduler",
            raw_payload={"rows": movers},
            feed_source="yfinance_movers",
        )
    except Exception as e:
        logger.warning("[IngestionHook] Top movers candidate failed: %s", e)

    # Persist to BigQuery permanently (never deleted)
    try:
        from .mcp_server.persist import persist_pipeline_snapshot
        summary = f"{count} movers: " + ", ".join(
            f"{m['ticker']} {m['change_pct']:+.1f}%" for m in movers[:5]
        )
        persist_pipeline_snapshot("top_movers", movers, summary)
    except Exception as e:
        logger.debug("[DailyPipeline] BQ persist skipped: %s", e)

    return {"price_movements_added": count}


async def _ingest_macro_snapshot(knowledge_store) -> dict:
    """Ingest macro snapshot merging yFinance market data with FRED economic indicators."""
    ensure_capability("scheduler", "market_data_read")
    from .connectors.macro import MacroHealthConnector
    data = await MacroHealthConnector().fetch_data()
    ind = dict(data.get("indicators") or {})

    # Merge FRED economic data (rates, CPI, unemployment, treasury yields)
    try:
        from .connectors import fetch_macro_snapshot
        fred_data = await fetch_macro_snapshot()
        if fred_data:
            for key in ("fed_funds_rate", "cpi_yoy", "treasury_10y", "treasury_2y",
                         "unemployment", "m2_supply"):
                if fred_data.get(key) is not None:
                    ind[key] = fred_data[key]
            ind["fred_fetched_at"] = fred_data.get("fetched_at", "")
            # Build a macro narrative from real data
            parts = []
            if ind.get("fed_funds_rate") is not None:
                parts.append(f"Fed Funds at {ind['fed_funds_rate']}%")
            if ind.get("cpi_yoy") is not None:
                parts.append(f"CPI YoY {ind['cpi_yoy']}%")
            if ind.get("unemployment") is not None:
                parts.append(f"unemployment {ind['unemployment']}%")
            vix = ind.get("vix_level")
            if vix is not None:
                parts.append(f"VIX at {vix:.1f}")
            if parts:
                ind["macro_narrative"] = "; ".join(parts)
    except Exception as e:
        logger.warning(f"[DailyPipeline] FRED merge failed: {e}")

    try:
        from .ingestion_agent import emit_ingestion_candidate
        await emit_ingestion_candidate(
            source_type="macro_pull",
            symbols=[],
            triggered_by="scheduler",
            raw_payload=data,
            feed_source="yfinance/fred",
        )
        if data.get("reconciled_capital_flows"):
            await emit_ingestion_candidate(
                source_type="capital_flow_pull",
                symbols=["SPY", "EFA", "EWJ", "TLT", "GLD", "BIL"],
                triggered_by="scheduler",
                raw_payload=data.get("reconciled_capital_flows"),
                feed_source="capital_flows",
            )
    except Exception as e:
        logger.warning("[IngestionHook] Macro/Flow candidate failed: %s", e)

    return {"macro_snapshot_added": True, "keys": list(ind.keys())}


def _run_data_lake_incremental_sync() -> dict:
    """
    Phase 7 — append recent OHLCV, rotate event re-ingest, weekly insider/recs (Mondays).
    Runs in a thread from the async pipeline so yfinance/pandas do not block the event loop.
    """
    from datetime import datetime, timezone

    out: dict = {}
    try:
        from .data_lake import incremental as dinc

        out.update(dinc.append_recent_ohlcv(tickers=None, extra_days=6))
        out.update(dinc.run_daily_event_slice())
        if datetime.now(timezone.utc).weekday() == 0:
            out.update(dinc.run_weekly_insider_and_recommendations())
    except Exception as e:
        out["data_lake_incremental_error"] = str(e)
        logger.warning("[DailyPipeline] data lake incremental: %s", e)
    return out


async def _ingest_youtube(knowledge_store) -> dict:
    """Fetch latest finance videos and store in youtube_insights collection."""
    ensure_capability("scheduler", "news_ingest")
    from .connectors.youtube import fetch_finance_videos
    videos = await fetch_finance_videos(hours_back=24)
    count = 0
    for video in videos:
        knowledge_store.add_youtube_insight(
            channel=video["channel"],
            title=video["title"],
            description=video["description"],
            published=video["published"],
            tags=video.get("tags", []),
        )
        count += 1
    return {"youtube_videos_added": count}


# Legacy _track_swarm_outcomes was removed here (maintainability refactoring — unified with outcome_grader.run_grader_pass).


def start_scheduler(knowledge_store, llm_client=None) -> None:
    """
    Start the APScheduler background scheduler.
    Schedules daily_pipeline to run at midnight UTC.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            _pipeline_job,
            trigger="cron",
            hour=0,
            minute=0,
            args=[knowledge_store, llm_client],
            id="daily_knowledge_pipeline",
            replace_existing=True,
        )
        scheduler.add_job(
            _l1_market_refresh_job,
            trigger="interval",
            minutes=15,
            id="market_l1_cache_refresh",
            replace_existing=True,
        )
        hb_min = max(5, int(os.environ.get("CORAL_HEARTBEAT_MINUTES", "30")))
        scheduler.add_job(
            _coral_heartbeat_job,
            trigger="interval",
            minutes=hb_min,
            args=[knowledge_store, llm_client],
            id="coral_heartbeat",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.add_job(
            _dreaming_job,
            trigger="cron",
            hour=1,
            minute=40,
            args=[knowledge_store, llm_client],
            id="coral_dreaming",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.add_job(
            _meta_harness_weekly_job,
            trigger="cron",
            day_of_week="sun",
            hour=3,
            minute=10,
            id="meta_harness_weekly",
            replace_existing=True,
            max_instances=1,
        )

        # Outcome grader (Harness Engineering Phase 2) — daily 02:10 UTC. Runs
        # AFTER the 00:00 ingest + dreaming but BEFORE the weekly meta-harness
        # so SEPL Reflect consumers always see freshly-graded decisions. The
        # job is a no-op when ``DECISION_LEDGER_ENABLE=0``.
        if os.environ.get("DECISION_LEDGER_ENABLE", "1") in ("1", "true", "TRUE", "yes", "on"):
            scheduler.add_job(
                _outcome_grader_job,
                trigger="cron",
                hour=2,
                minute=10,
                id="outcome_grader_daily",
                replace_existing=True,
                max_instances=1,
            )
            grader_msg = " + outcome grader 02:10 UTC"

            # Predictor self-learning is retired once the brain owns the predictor surface.
            _predictor_learning_on = os.environ.get("PREDICTOR_SELF_LEARNING_ENABLE", "1").strip().lower() in (
                "1", "true", "yes", "on",
            )
            try:
                from .brain.flags import brain_surface_enabled

                if brain_surface_enabled("predictor"):
                    _predictor_learning_on = False
            except Exception:
                pass
            if _predictor_learning_on:
                scheduler.add_job(
                    _predictor_self_learning_job,
                    trigger="cron",
                    hour=2,
                    minute=40,
                    id="predictor_self_learning_daily",
                    replace_existing=True,
                    max_instances=1,
                )
                grader_msg += " + predictor self-learning 02:40 UTC"
        else:
            grader_msg = " + outcome grader DISABLED"

        # Portfolio snapshots (Your Morning v0) — after US cash close (~22:30 UTC).
        scheduler.add_job(
            _portfolio_snapshots_job,
            trigger="cron",
            hour=22,
            minute=30,
            id="portfolio_snapshots_daily",
            replace_existing=True,
            max_instances=1,
        )
        snap_msg = " + portfolio snapshots 22:30 UTC"

        # Narrative Rotation Radar — daily 00:50 UTC (after the 00:00 ingest so it
        # scores on fresh prices). Additive + feature-flagged; never blocks startup.
        nr_msg = ""
        if os.environ.get("NARRATIVE_RADAR_ENABLE", "1").strip() != "0":
            scheduler.add_job(
                _narrative_radar_job,
                trigger="cron",
                hour=0,
                minute=50,
                id="narrative_radar_daily",
                replace_existing=True,
                max_instances=1,
            )
            nr_msg = " + narrative radar 00:50 UTC"

        scheduler.start()
        logger.info(
            "[DailyPipeline] APScheduler started — daily 00:00 UTC + L1 every 15m + "
            "CORAL heartbeat every %dm + dreaming 01:40 UTC + meta-harness Sun 03:10 UTC%s%s",
            hb_min, grader_msg, snap_msg + nr_msg,
        )
    except Exception as e:
        logger.warning(f"[DailyPipeline] Scheduler start failed: {e}")


async def _pipeline_job(knowledge_store, llm_client=None) -> None:
    """Async wrapper for the scheduler to call run_daily_pipeline."""
    await run_daily_pipeline(knowledge_store, llm_client=llm_client)


async def _l1_market_refresh_job() -> None:
    """Refresh in-memory quotes/macro for chat L1 (no per-message SQLite)."""
    try:
        from .market_l1_cache import refresh

        await refresh()
    except Exception as e:
        logger.warning(f"[DailyPipeline] L1 market refresh failed: {e}")


async def _coral_heartbeat_job(knowledge_store, llm_client=None) -> None:
    """Periodic CORAL hub notes: legacy heartbeat + per-agent reflections (see coral_heartbeat)."""
    try:
        from .coral_heartbeat import run_coral_agent_reflections, run_coral_heartbeat

        await run_coral_heartbeat(knowledge_store, llm_client)
        await run_coral_agent_reflections(knowledge_store, llm_client)
    except Exception as e:
        logger.warning("[DailyPipeline] coral heartbeat failed: %s", e)


async def _dreaming_job(knowledge_store, llm_client=None) -> None:
    """Nightly digest of handoff events into CORAL (see coral_dreaming)."""
    try:
        from .coral_dreaming import run_dreaming_job

        await run_dreaming_job(knowledge_store, llm_client)
    except Exception as e:
        logger.warning("[DailyPipeline] coral dreaming failed: %s", e)


async def _narrative_radar_job() -> None:
    """Daily Narrative Rotation Radar scan — theme-lifecycle snapshot + alerts +
    theme_phase ledger emit. Never raises (best-effort like other scheduled jobs)."""
    try:
        import uuid

        from .narrative_radar import engine as nr_engine

        result = await nr_engine.run_scan(uuid.uuid4().hex, force=True)
        logger.info("[DailyPipeline] narrative_radar scan result=%s", result)
    except Exception as e:
        logger.warning("[DailyPipeline] narrative radar job failed: %s", e)


async def _outcome_grader_job() -> None:
    """Grade every horizon (1d/5d/21d/63d) against SPY-relative market truth.

    Harness Engineering Phase 2 — replaces ``_track_swarm_outcomes`` as the
    primary learning signal for SEPL. Writes into ``outcome_observations``
    so correlation queries can score every decision type (not just swarm).
    """
    try:
        from .outcome_grader import run_grader_pass

        result = await run_grader_pass()
        logger.info("[DailyPipeline] outcome_grader_pass result=%s", result)
    except Exception as e:
        logger.warning("[DailyPipeline] outcome grader failed: %s", e)


async def _predictor_self_learning_job() -> None:
    """Nightly self-learning pass (Phase 3) — runs AFTER the 02:10 grader.

    1. Conformal rollback guard, then recalibrate q10–q90 band scales from
       fresh ``forecast_band_hit`` coverage (versioned in the registry).
    2. Refresh learned ensemble weights from walk-forward data-lake replay.
    3. Regenerate market-truth SEPL fixtures from graded decisions.

    Every step is kill-switched independently and never raises.
    """
    import asyncio as _asyncio

    try:
        from .predictor.conformal import maybe_rollback, nightly_conformal_update

        rb = await _asyncio.to_thread(maybe_rollback)
        cf = await _asyncio.to_thread(nightly_conformal_update)
        logger.info("[DailyPipeline] conformal rollback=%s update=%s", rb, cf)
    except Exception as e:
        logger.warning("[DailyPipeline] conformal step failed: %s", e)

    try:
        from .predictor.learned_weights import nightly_weights_update

        lw = await _asyncio.to_thread(nightly_weights_update)
        logger.info("[DailyPipeline] learned_weights update=%s", lw)
    except Exception as e:
        logger.warning("[DailyPipeline] learned weights step failed: %s", e)

    try:
        from .sepl_market_fixtures import regenerate_fixtures

        fx = await _asyncio.to_thread(regenerate_fixtures)
        logger.info("[DailyPipeline] sepl_market_fixtures=%s", fx)
    except Exception as e:
        logger.warning("[DailyPipeline] market fixtures step failed: %s", e)


async def _portfolio_snapshots_job() -> None:
    """Per-user portfolio snapshots for Your Morning (idempotent per user+date)."""
    try:
        from .portfolio_snapshots_job import run_portfolio_snapshots_job

        result = await run_portfolio_snapshots_job()
        logger.info("[DailyPipeline] portfolio_snapshots result=%s", result)
    except Exception as e:
        logger.warning("[DailyPipeline] portfolio snapshots failed: %s", e)


async def _meta_harness_weekly_job() -> None:
    """Log a JSON-shaped aggregate for operators (handoffs, attempts, claim counts) — no LLM."""
    try:
        from .meta_harness.report import build_meta_harness_report

        rep = build_meta_harness_report(since_days=7.0)
        he = rep.get("handoff_events") or {}
        ca = rep.get("coral_attempts") or {}
        cs = rep.get("claim_store") or {}
        logger.info(
            "[MetaHarness] weekly snapshot events=%s attempts=%s claim_entities=%s active_claims=%s",
            he.get("count"),
            ca.get("count"),
            cs.get("entities"),
            cs.get("active_claims"),
        )
    except Exception as e:
        logger.warning("[DailyPipeline] meta-harness weekly job failed: %s", e)
