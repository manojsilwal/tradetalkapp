"""Knowledge store endpoints — stats, export, pipeline, S&P 500."""
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from fastapi.responses import Response

from ..cron_auth import require_cron_secret
from ..rate_limiter import rate_limit
from ..deps import knowledge_store

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_rl_export = rate_limit("export")


@router.get("/stats")
async def knowledge_stats():
    """Returns entry counts per collection and pipeline status."""
    return knowledge_store.stats()


@router.get("/export", dependencies=[Depends(_rl_export)])
async def export_knowledge():
    """Download all debate + backtest history as a JSONL fine-tuning file."""
    jsonl_content = knowledge_store.export_jsonl()
    return Response(
        content=jsonl_content,
        media_type="application/jsonl",
        headers={"Content-Disposition": "attachment; filename=tradetalk_training_data.jsonl"},
    )


@router.get("/pipeline-status")
async def pipeline_status():
    """Returns status of the last daily knowledge pipeline run."""
    stats = knowledge_store.stats()
    return {
        "pipeline_status": stats.get("pipeline_status", {}),
        "collection_sizes": stats.get("collections", {}),
    }


@router.get("/reflections")
async def knowledge_reflections(n: int = 20):
    """Debug endpoint to inspect recently stored reflection memories."""
    n = max(1, min(n, 100))
    reflections = knowledge_store.get_recent_reflections(n=n)
    return {"reflections": reflections, "total": len(reflections)}


@router.post("/pipeline-run", dependencies=[Depends(require_cron_secret)])
async def trigger_pipeline(background_tasks: BackgroundTasks):
    """Run the daily knowledge pipeline in the background."""
    from ..daily_pipeline import run_daily_pipeline
    import logging

    async def _bg_run():
        try:
            await run_daily_pipeline(knowledge_store)
        except Exception as e:
            logging.error(f"[KnowledgeRouter] Background pipeline failed: {e}")

    background_tasks.add_task(_bg_run)
    return {"status": "accepted", "message": "Pipeline triggered in background"}


@router.post("/sp500-ingest", dependencies=[Depends(require_cron_secret)])
async def trigger_sp500_ingestion(background_tasks: BackgroundTasks, tickers: list[str] = None):
    """Trigger the S&P 500 ingestion pipeline in the background."""
    from ..sp500_ingestion_pipeline import run_sp500_ingestion
    import logging

    async def _bg_ingest():
        try:
            await run_sp500_ingestion(tickers=tickers)
        except Exception as e:
            logging.error(f"[KnowledgeRouter] Background S&P500 ingestion failed: {e}")

    background_tasks.add_task(_bg_ingest)
    return {"status": "accepted", "message": "S&P 500 ingestion triggered in background"}


@router.get("/sp500-stats")
async def sp500_ingestion_stats():
    """Returns counts for the S&P 500 vector collections."""
    stats = knowledge_store.stats()
    collections = stats.get("collections", {})
    return {
        "sp500_fundamentals_narratives": collections.get("sp500_fundamentals_narratives", 0),
        "sp500_sector_analysis":         collections.get("sp500_sector_analysis", 0),
        "stock_profiles":                collections.get("stock_profiles", 0),
        "earnings_memory":               collections.get("earnings_memory", 0),
        "vector_backend":                stats.get("vector_backend", "unknown"),
    }
