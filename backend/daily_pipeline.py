"""
Daily Knowledge Pipeline — orchestrates all 4 knowledge source ingestion jobs.
Runs every night at midnight via APScheduler.
Each run adds new records to ChromaDB; all historical data remains searchable.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def run_daily_pipeline(knowledge_store) -> dict:
    """
    Run all 4 ingestion jobs concurrently.
    Returns a summary dict logged and stored in knowledge_store.pipeline_status.
    """
    logger.info("[DailyPipeline] Starting daily knowledge ingestion...")
    today = str(datetime.now(timezone.utc).date())
    summary = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "price_movements_added": 0,
        "youtube_videos_added": 0,
        "macro_snapshot_added": False,
        "errors": [],
    }

    results = await asyncio.gather(
        _ingest_price_movements(knowledge_store),
        _ingest_macro_snapshot(knowledge_store),
        _ingest_youtube(knowledge_store),
        return_exceptions=True,
    )

    for i, result in enumerate(results):
        job_name = ["price_movements", "macro_snapshot", "youtube"][i]
        if isinstance(result, Exception):
            summary["errors"].append(f"{job_name}: {result}")
            logger.warning(f"[DailyPipeline] {job_name} failed: {result}")
        elif isinstance(result, dict):
            summary.update(result)

    knowledge_store.update_pipeline_status(**summary)
    logger.info(f"[DailyPipeline] Complete — {summary}")
    return summary


async def _ingest_price_movements(knowledge_store) -> dict:
    """Fetch top S&P 500 movers and store in price_movements collection."""
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
    """Fetch FRED macro indicators and store in macro_snapshots collection."""
    from .connectors.fred import fetch_macro_snapshot
    snapshot = await fetch_macro_snapshot()
    if snapshot:
        knowledge_store.add_macro_snapshot(snapshot)
        return {"macro_snapshot_added": True}
    return {"macro_snapshot_added": False}


async def _ingest_youtube(knowledge_store) -> dict:
    """Fetch latest finance videos and store in youtube_insights collection."""
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


def start_scheduler(knowledge_store) -> None:
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
            args=[knowledge_store],
            id="daily_knowledge_pipeline",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("[DailyPipeline] APScheduler started — daily run at 00:00 UTC")
    except Exception as e:
        logger.warning(f"[DailyPipeline] Scheduler start failed: {e}")


async def _pipeline_job(knowledge_store) -> None:
    """Async wrapper for the scheduler to call run_daily_pipeline."""
    await run_daily_pipeline(knowledge_store)
