import asyncio
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Query
from typing import Optional

from .. import fund_leaderboard_store as store
from .. import fund_leaderboard_job as job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/funds", tags=["funds"])

_EMPTY_DISCLAIMER = (
    "13F-derived returns are partial public long-book estimates, not actual fund "
    "returns. They exclude shorts, leverage, cash, and non-U.S. holdings."
)


def _empty_leaderboard(mode: str) -> dict:
    return {
        "asOfDate": None,
        "latestReportPeriod": None,
        "methodologyVersion": store.METHODOLOGY_VERSION,
        "mode": mode,
        "disclaimer": _EMPTY_DISCLAIMER,
        "rows": [],
        "message": (
            "No leaderboard snapshot yet. Trigger ingestion via "
            "POST /api/funds/ingest/run to populate from SEC 13F filings."
        ),
    }


@router.get("/leaderboard")
async def get_fund_leaderboard(
    period: str = Query("5Y"),
    mode: str = Query("13f_investable"),
    rankingMode: str = Query("risk_adjusted_default"),
    strategy: str = Query("all"),
    sector: str = Query("all"),
    minTrackRecordQuarters: int = Query(8),
    excludeIndexManagers: bool = Query(True),
    minConfidence: int = Query(0),
    latestReportPeriod: str = Query("auto"),
    limit: int = Query(50),
    offset: int = Query(0),
):
    """Fund leaderboard rankings based on 13F clone performance (DB-backed)."""
    try:
        store.init_schema()
        result = store.get_leaderboard(
            mode=mode, limit=limit, offset=offset, min_confidence=minConfidence
        )
        if not result.get("rows"):
            return _empty_leaderboard(mode)
        return result
    except Exception as e:
        logger.warning("[FundLeaderboard] leaderboard query failed: %s", e)
        return _empty_leaderboard(mode)


@router.get("/{fundId}/portfolio/latest")
async def get_fund_portfolio_latest(fundId: str):
    """Latest 13F portfolio holdings and sector allocation for a fund."""
    store.init_schema()
    data = store.get_fund_portfolio_latest(fundId)
    if not data:
        raise HTTPException(status_code=404, detail="Fund portfolio not found")
    return data


@router.get("/{fundId}/returns")
async def get_fund_returns(
    fundId: str,
    mode: str = Query("13f_investable"),
    period: str = Query("5Y"),
    benchmark: str = Query("SPY"),
):
    """Time-series return data and performance metrics for a fund."""
    store.init_schema()
    data = store.get_fund_returns(fundId, mode=mode, period=period)
    if not data:
        raise HTTPException(status_code=404, detail="Fund returns not found")
    return data


@router.get("/{fundId}/quarterly-report")
async def get_fund_quarterly_report(fundId: str):
    """Quarterly report summary for a fund."""
    store.init_schema()
    data = store.get_fund_quarterly_report(fundId)
    if not data:
        raise HTTPException(status_code=404, detail="Fund report not found")
    return data


def _check_admin(token: Optional[str]) -> None:
    """Require X-Admin-Token to match FUND_LB_ADMIN_TOKEN when that env is set."""
    expected = os.environ.get("FUND_LB_ADMIN_TOKEN", "").strip()
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin token")


@router.post("/ingest/run")
async def run_ingestion(
    universeSize: int = Query(job.DEFAULT_UNIVERSE_SIZE),
    topN: int = Query(job.DEFAULT_TOP_N),
    maxQuarters: int = Query(job.DEFAULT_MAX_QUARTERS),
    discoveryMax: Optional[int] = Query(None),
    x_admin_token: Optional[str] = Header(None),
):
    """Kick the 13F ingestion + ranking pipeline in the background."""
    _check_admin(x_admin_token)
    state = job.get_run_state()
    if state.get("status") == "running":
        return {"status": "already_running", "state": state}

    async def _runner():
        await job.run_fund_leaderboard_job(
            universe_size=universeSize,
            top_n=topN,
            max_quarters=maxQuarters,
            discovery_max=discoveryMax,
        )

    asyncio.create_task(_runner())
    return {"status": "started", "universeSize": universeSize, "topN": topN}


@router.get("/ingest/status")
async def ingestion_status():
    """Current state of the ingestion job + how many funds are populated."""
    store.init_schema()
    return {"run": job.get_run_state(), "fundsTracked": store.count_funds()}
