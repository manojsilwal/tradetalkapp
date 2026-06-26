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


def _resolve_fund(identifier: str) -> Optional[dict]:
    """Resolve a fund by CIK (preferred) or fund_id."""
    fund = store.get_fund_by_cik(identifier)
    if not fund:
        try:
            fund = store.get_fund(identifier)
        except Exception:
            fund = None
    return fund


@router.get("/top")
async def get_top_funds(
    mode: str = Query("13f_investable"),
    limit: int = Query(50),
    offset: int = Query(0),
    minConfidence: int = Query(0),
):
    """Top funds for the current snapshot (value- or return-ranked per ingest mode)."""
    try:
        store.init_schema()
        result = store.get_leaderboard(mode=mode, limit=limit, offset=offset, min_confidence=minConfidence)
        if not result.get("rows"):
            return _empty_leaderboard(mode)
        return result
    except Exception as e:
        logger.warning("[FundLeaderboard] /top query failed: %s", e)
        return _empty_leaderboard(mode)


@router.get("/{cik}/filings")
async def get_fund_filings(cik: str, limit: int = Query(24)):
    """List 13F filings for a manager keyed by CIK (or fund_id)."""
    store.init_schema()
    fund = _resolve_fund(cik)
    if not fund:
        raise HTTPException(status_code=404, detail="Fund not found")
    filings = store.get_filings_for_fund(fund["fund_id"], limit=limit)
    return {
        "cik": fund.get("cik"),
        "fundId": fund["fund_id"],
        "fundName": fund.get("display_name"),
        "filings": [
            {
                "accessionNumber": f.get("accession_number"),
                "formType": f.get("form_type"),
                "reportPeriod": f.get("report_period"),
                "filingDate": f.get("filing_date"),
                "filingUrl": f.get("filing_url"),
                "totalMarketValueUsd": f.get("total_market_value_usd"),
                "parseStatus": f.get("parse_status"),
            }
            for f in filings
        ],
    }


@router.get("/{cik}/holdings")
async def get_fund_holdings(cik: str, period: str = Query("latest")):
    """Holdings for a manager for a given report period (default latest)."""
    store.init_schema()
    fund = _resolve_fund(cik)
    if not fund:
        raise HTTPException(status_code=404, detail="Fund not found")
    fund_id = fund["fund_id"]
    if period and period != "latest":
        holdings = store.get_holdings_for_period(fund_id, period)
        report_period = period
    else:
        latest = store.get_latest_filing(fund_id)
        if not latest:
            raise HTTPException(status_code=404, detail="No filings for fund")
        holdings = store.get_holdings_for_filing(latest["filing_id"])
        report_period = latest.get("report_period")
    total_mv = sum((h.get("market_value_usd") or 0) for h in holdings)
    return {
        "cik": fund.get("cik"),
        "fundId": fund_id,
        "fundName": fund.get("display_name"),
        "reportPeriod": report_period,
        "totalMarketValueUsd": total_mv,
        "holdings": [
            {
                "ticker": h.get("ticker"),
                "companyName": h.get("issuer_name"),
                "cusip": h.get("cusip"),
                "sector": h.get("sector"),
                "shares": h.get("shares"),
                "marketValueUsd": h.get("market_value_usd"),
                "weight": h.get("holding_weight"),
                "mappingStatus": h.get("mapping_status"),
            }
            for h in holdings
        ],
    }


@router.get("/{cik}/changes")
async def get_fund_changes(cik: str, period: str = Query("latest")):
    """Position changes (new/sold-out/increased/decreased) + sector flow for a period."""
    store.init_schema()
    fund = _resolve_fund(cik)
    if not fund:
        raise HTTPException(status_code=404, detail="Fund not found")
    summary = store.get_quarterly_summary(
        fund["fund_id"], None if period in ("latest", "", None) else period
    )
    if not summary:
        raise HTTPException(status_code=404, detail="No quarterly summary for period")
    return {
        "cik": fund.get("cik"),
        "fundId": fund["fund_id"],
        "fundName": fund.get("display_name"),
        "periodOfReport": summary.get("period_of_report"),
        "prevPeriod": summary.get("prev_period"),
        "total13FValueUsd": summary.get("total_13f_value_usd"),
        "holdingsCount": summary.get("holdings_count"),
        "top10Concentration": summary.get("top10_concentration"),
        "top20Concentration": summary.get("top20_concentration"),
        "turnoverEstimatePct": summary.get("turnover_estimate_pct"),
        "counts": {
            "new": summary.get("new_count"),
            "soldOut": summary.get("soldout_count"),
            "increased": summary.get("increased_count"),
            "decreased": summary.get("decreased_count"),
            "unchanged": summary.get("unchanged_count"),
        },
        "changes": summary.get("changes"),
        "sectorFlow": summary.get("sector_flow"),
    }


@router.get("/{cik}/timeline")
async def get_fund_timeline(cik: str):
    """Per-period timeline of value, holdings count, concentration, and turnover."""
    store.init_schema()
    fund = _resolve_fund(cik)
    if not fund:
        raise HTTPException(status_code=404, detail="Fund not found")
    summaries = store.list_quarterly_summaries(fund["fund_id"])
    return {
        "cik": fund.get("cik"),
        "fundId": fund["fund_id"],
        "fundName": fund.get("display_name"),
        "timeline": [
            {
                "periodOfReport": s.get("period_of_report"),
                "total13FValueUsd": s.get("total_13f_value_usd"),
                "holdingsCount": s.get("holdings_count"),
                "top10Concentration": s.get("top10_concentration"),
                "turnoverEstimatePct": s.get("turnover_estimate_pct"),
                "newCount": s.get("new_count"),
                "soldoutCount": s.get("soldout_count"),
                "increasedCount": s.get("increased_count"),
                "decreasedCount": s.get("decreased_count"),
            }
            for s in summaries
        ],
    }


def _check_admin(token: Optional[str]) -> None:
    """Require X-Admin-Token to match FUND_LB_ADMIN_TOKEN when that env is set."""
    expected = os.environ.get("FUND_LB_ADMIN_TOKEN", "").strip()
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin token")


@router.post("/ingest/run")
async def run_ingestion(
    rankingMode: str = Query("SEC_13F_VALUE"),
    universeSize: int = Query(job.DEFAULT_UNIVERSE_SIZE),
    topN: int = Query(job.DEFAULT_TOP_N),
    maxQuarters: int = Query(job.DEFAULT_MAX_QUARTERS),
    discoveryMax: Optional[int] = Query(None),
    saveRaw: bool = Query(False),
    x_admin_token: Optional[str] = Header(None),
):
    """Kick the 13F ingestion + ranking pipeline in the background."""
    _check_admin(x_admin_token)
    state = job.get_run_state()
    if state.get("status") == "running":
        return {"status": "already_running", "state": state}

    async def _runner():
        await job.run_fund_leaderboard_job(
            ranking_mode=rankingMode,
            universe_size=universeSize,
            top_n=topN,
            max_quarters=maxQuarters,
            discovery_max=discoveryMax,
            save_raw=saveRaw,
        )

    asyncio.create_task(_runner())
    return {"status": "started", "rankingMode": rankingMode, "universeSize": universeSize, "topN": topN}


@router.get("/ingest/status")
async def ingestion_status():
    """Current state of the ingestion job + how many funds are populated."""
    store.init_schema()
    return {"run": job.get_run_state(), "fundsTracked": store.count_funds()}
