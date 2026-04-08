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

    jobs = [
        _ingest_price_movements(knowledge_store),
        _ingest_macro_snapshot(knowledge_store),
        _ingest_youtube(knowledge_store),
    ]
    job_names = ["price_movements", "macro_snapshot", "youtube"]
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

    # Outcome tracking runs after ingestion so price data is fresh
    try:
        outcome_result = await _track_swarm_outcomes(knowledge_store, llm_client)
        summary.update(outcome_result)
    except Exception as e:
        summary["errors"].append(f"swarm_outcomes: {e}")
        logger.warning(f"[DailyPipeline] swarm outcome tracking failed: {e}")

    knowledge_store.update_pipeline_status(**summary)
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
        fred_data = await asyncio.to_thread(fetch_macro_snapshot)
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

    knowledge_store.add_macro_snapshot(ind)
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


async def _track_swarm_outcomes(knowledge_store, llm_client=None) -> dict:
    """
    Fetch yesterday's swarm analyses, compare signals to T+1 price change,
    and write LLM-generated reflections for future learning.
    """
    ensure_capability("scheduler", "market_data_read")
    import yfinance as yf
    from datetime import timedelta

    col = knowledge_store._safe_col("swarm_history")
    if not col or col.count() == 0:
        return {"swarm_outcomes_tracked": 0}

    yesterday = str((datetime.now(timezone.utc) - timedelta(days=1)).date())
    rows = col.get(include=["documents", "metadatas"])
    all_metas = rows.get("metadatas", [])

    yesterday_analyses = [m for m in all_metas if m and m.get("date") == yesterday]
    if not yesterday_analyses:
        return {"swarm_outcomes_tracked": 0}

    tracked = 0
    for meta in yesterday_analyses:
        ticker = meta.get("ticker", "")
        if not ticker:
            continue
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")
            if hist.empty or len(hist) < 2:
                continue
            price_yesterday = hist["Close"].iloc[-2]
            price_today = hist["Close"].iloc[-1]
            price_change_pct = ((price_today - price_yesterday) / price_yesterday) * 100

            signal = int(meta.get("confidence", 0.5) > 0.5)  # simplified
            verdict = meta.get("verdict", "NEUTRAL")
            confidence = float(meta.get("confidence", 0.5))
            regime = meta.get("market_regime", "BULL_NORMAL") if "market_regime" in meta else "BULL_NORMAL"

            was_bullish = "BUY" in verdict.upper() or "STRONG" in verdict.upper()
            correct = (was_bullish and price_change_pct > 0) or (not was_bullish and price_change_pct <= 0)

            lesson = f"Signal was {'correct' if correct else 'incorrect'} — verdict {verdict} vs {price_change_pct:+.1f}% move."
            if llm_client:
                try:
                    llm_result = await llm_client.generate_swarm_reflection(
                        ticker, 1 if was_bullish else 0, verdict,
                        confidence, price_change_pct, regime,
                    )
                    lesson = llm_result.get("lesson", lesson)
                except Exception:
                    pass

            knowledge_store.add_swarm_reflection(
                ticker=ticker, signal=1 if was_bullish else 0,
                verdict=verdict, confidence=confidence,
                price_change_pct=price_change_pct,
                lesson=lesson, regime=regime, correct=correct,
            )
            tracked += 1
        except Exception as e:
            logger.warning(f"[DailyPipeline] outcome tracking for {ticker} failed: {e}")
            continue

    logger.info(f"[DailyPipeline] Tracked outcomes for {tracked} swarm analyses")
    return {"swarm_outcomes_tracked": tracked}


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
        scheduler.start()
        logger.info(
            "[DailyPipeline] APScheduler started — daily 00:00 UTC + L1 every 15m + "
            "CORAL heartbeat every %dm + dreaming 01:40 UTC + meta-harness Sun 03:10 UTC",
            hb_min,
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
