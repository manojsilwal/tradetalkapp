"""
Daily Knowledge Pipeline — orchestrates all 4 knowledge source ingestion jobs.
Runs every night at midnight via APScheduler.
Each run adds new records to ChromaDB; all historical data remains searchable.
"""
import asyncio
import logging
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
    """Fetch FRED macro indicators and store in macro_snapshots collection."""
    ensure_capability("scheduler", "market_data_read")
    from .connectors.fred import fetch_macro_snapshot
    snapshot = await fetch_macro_snapshot()
    if snapshot:
        knowledge_store.add_macro_snapshot(snapshot)
        return {"macro_snapshot_added": True}
    return {"macro_snapshot_added": False}


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
        scheduler.start()
        logger.info("[DailyPipeline] APScheduler started — daily run at 00:00 UTC")
    except Exception as e:
        logger.warning(f"[DailyPipeline] Scheduler start failed: {e}")


async def _pipeline_job(knowledge_store, llm_client=None) -> None:
    """Async wrapper for the scheduler to call run_daily_pipeline."""
    await run_daily_pipeline(knowledge_store, llm_client=llm_client)
