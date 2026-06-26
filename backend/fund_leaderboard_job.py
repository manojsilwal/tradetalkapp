"""
Fund Leaderboard batch job.

Orchestrates the full 13F clone-return pipeline:
1. Auto-discover the largest 13F filers by AUM (backend/coral_skills/sec_universe).
2. Pull ~5 years (20 quarters) of 13F-HR filings per manager.
3. Map CUSIPs to tickers (OpenFIGI, cached) and persist filings + holdings.
4. Reconstruct the investable clone return series and metrics per manager.
5. Score + rank, keep the top 50, and persist a leaderboard snapshot.

This is a heavy, network-bound job (minutes). It is designed to run in the
background via the /api/funds/ingest/run endpoint or a weekly scheduler. All
network failures degrade gracefully so a single bad manager never aborts the run.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from . import fund_leaderboard_store as store

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE_SIZE = int(os.environ.get("FUND_LB_UNIVERSE_SIZE", "150"))
DEFAULT_TOP_N = int(os.environ.get("FUND_LB_TOP_N", "50"))
DEFAULT_MAX_QUARTERS = int(os.environ.get("FUND_LB_MAX_QUARTERS", "20"))
EXPECTED_QUARTERS = DEFAULT_MAX_QUARTERS
PERIOD_LABEL = "5Y"
MODE = store.DEFAULT_MODE
BENCHMARK = "SPY"

# Skip managers whose mapped market value is too small to proxy returns.
MIN_MAPPED_PCT = float(os.environ.get("FUND_LB_MIN_MAPPED_PCT", "0.40"))
MIN_QUARTERS = int(os.environ.get("FUND_LB_MIN_QUARTERS", "8"))

_run_state: Dict[str, Any] = {"status": "idle", "started_at": None, "summary": None}


def get_run_state() -> Dict[str, Any]:
    return dict(_run_state)


def _build_snapshots_and_persist(
    fund_id: str,
    parsed_filings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Persist filings/holdings and build weighted snapshots for reconstruction.

    Returns dict with `snapshots` (for calculate_clone_returns), `tickers`,
    mapped/total market value, and latest-filing summary fields.
    """
    snapshots: List[Dict[str, Any]] = []
    all_tickers: set = set()
    mapped_mv_total = 0.0
    total_mv_total = 0.0
    latest_total_mv = 0.0
    latest_report_period = None
    latest_filing_date = None

    # parsed_filings are most-recent-first; reverse to chronological for snapshots.
    for filing in sorted(parsed_filings, key=lambda f: f.get("filing_date") or ""):
        holdings = filing.get("holdings", [])
        total_mv = sum((h.get("market_value_usd") or 0.0) for h in holdings)
        if total_mv <= 0:
            continue

        filing_id = store.upsert_filing(
            fund_id=fund_id,
            cik=filing["cik"],
            accession_number=filing["accession_number"],
            form_type=filing.get("form_type", "13F-HR"),
            report_period=filing.get("report_period"),
            filing_date=filing.get("filing_date"),
            filing_url=filing.get("filing_url"),
            total_market_value_usd=total_mv,
        )

        weighted_holdings = []
        store_holdings = []
        mapped_mv = 0.0
        for h in holdings:
            mv = h.get("market_value_usd") or 0.0
            weight = mv / total_mv if total_mv else 0.0
            ticker = h.get("ticker")
            if ticker:
                all_tickers.add(ticker)
                mapped_mv += mv
                weighted_holdings.append({"ticker": ticker, "weight": weight})
            store_holdings.append({
                "issuer_name": h.get("issuer_name"),
                "cusip": h.get("cusip"),
                "ticker": ticker,
                "sector": h.get("sector"),
                "shares": h.get("shares"),
                "market_value_usd": mv,
                "holding_weight": weight,
                "put_call": h.get("put_call"),
                "mapping_status": h.get("mapping_status", "unmapped"),
            })

        store.replace_holdings(filing_id, fund_id, filing.get("report_period"), store_holdings)

        mapped_mv_total += mapped_mv
        total_mv_total += total_mv
        latest_total_mv = total_mv
        latest_report_period = filing.get("report_period")
        latest_filing_date = filing.get("filing_date")

        if weighted_holdings:
            snapshots.append({
                "filing_date": filing.get("filing_date"),
                "report_period": filing.get("report_period"),
                "holdings": weighted_holdings,
            })

    return {
        "snapshots": snapshots,
        "tickers": sorted(all_tickers),
        "mapped_mv_total": mapped_mv_total,
        "total_mv_total": total_mv_total,
        "latest_total_mv": latest_total_mv,
        "latest_report_period": latest_report_period,
        "latest_filing_date": latest_filing_date,
    }


def _top_sector(fund_id: str) -> Dict[str, Any]:
    portfolio = store.get_fund_portfolio_latest(fund_id)
    if not portfolio or not portfolio.get("sectorAllocation"):
        return {}
    top = portfolio["sectorAllocation"][0]
    holdings = portfolio.get("holdings", [])
    top10 = sorted(holdings, key=lambda h: (h.get("weight") or 0), reverse=True)[:10]
    return {
        "topSector": top.get("sector"),
        "topSectorWeight": top.get("weight"),
        "top10HoldingsWeight": sum((h.get("weight") or 0) for h in top10),
    }


async def _process_manager(
    filer: Dict[str, Any],
    max_quarters: int,
) -> Optional[Dict[str, Any]]:
    """Ingest, map, reconstruct, and score a single manager. Returns a scoring dict."""
    from .coral_skills.sec_13f_ingestion import ingest_manager_13f_history
    from .coral_skills.security_mapper import map_holdings_to_tickers
    from .coral_skills.return_reconstruction import fetch_historical_prices, calculate_clone_returns
    from .coral_skills.leaderboard_scoring import calculate_data_confidence

    cik = filer["cik"]
    name = filer["name"]

    fund_id = store.upsert_fund(
        cik=cik,
        display_name=name,
        manager_type="institutional",
        latest_aum_usd=filer.get("aum_usd"),
    )

    history = await ingest_manager_13f_history(cik, fund_id, max_quarters=max_quarters)
    parsed = history.get("filings", [])
    if len(parsed) < MIN_QUARTERS:
        logger.info("[FundLB] %s (%s): only %d quarters, skipping", name, cik, len(parsed))
        return None

    # Map CUSIP -> ticker for each filing's holdings.
    for filing in parsed:
        filing["holdings"] = await map_holdings_to_tickers(filing.get("holdings", []))

    built = _build_snapshots_and_persist(fund_id, parsed)
    snapshots = built["snapshots"]
    tickers = built["tickers"]
    if not snapshots or not tickers:
        return None

    mapped_pct = (built["mapped_mv_total"] / built["total_mv_total"]) if built["total_mv_total"] else 0.0
    if mapped_pct < MIN_MAPPED_PCT:
        logger.info("[FundLB] %s (%s): mapped %.0f%% < min, skipping", name, cik, mapped_pct * 100)
        return None

    # Price window: from earliest filing to today.
    start_dates = [s["filing_date"] for s in snapshots if s.get("filing_date")]
    start = min(start_dates) if start_dates else (date.today() - timedelta(days=365 * 5)).isoformat()
    end = date.today().isoformat()

    try:
        prices_df = await fetch_historical_prices(tickers, start, end)
        bench_df = await fetch_historical_prices([BENCHMARK], start, end)
    except Exception as e:
        logger.warning("[FundLB] %s price fetch failed: %s", name, e)
        return None

    if prices_df is None or prices_df.empty:
        return None

    result = calculate_clone_returns(snapshots, prices_df, bench_df)
    if result.get("error"):
        logger.info("[FundLB] %s: reconstruction error %s", name, result["error"])
        return None

    metrics = result["metrics"]
    confidence = calculate_data_confidence(
        valid_quarters=len(snapshots),
        expected_quarters=EXPECTED_QUARTERS,
        mapped_market_value=built["mapped_mv_total"],
        total_market_value=built["total_mv_total"],
        priced_market_value=built["mapped_mv_total"],
    )

    store.upsert_return_metrics(
        fund_id=fund_id,
        mode=MODE,
        period=PERIOD_LABEL,
        as_of_date=end,
        metrics=metrics,
        data_confidence_score=confidence.get("score"),
        series=result.get("series"),
    )

    sector_info = _top_sector(fund_id)
    return {
        "fundId": fund_id,
        "fundName": name,
        "cik": cik,
        "managerType": "Institutional",
        "strategyTags": [],
        "metrics": metrics,
        "confidence": confidence,
        "latest13FValueUsd": built["latest_total_mv"],
        "latestReportPeriod": built["latest_report_period"],
        "lastFilingDate": built["latest_filing_date"],
        **sector_info,
    }


def _to_presentable_row(scored: Dict[str, Any]) -> Dict[str, Any]:
    m = scored.get("metrics", {})
    conf = scored.get("confidence", {})
    return {
        "rank": scored.get("rank"),
        "fundId": scored["fundId"],
        "fundName": scored["fundName"],
        "managerType": scored.get("managerType", "Institutional"),
        "strategyTags": scored.get("strategyTags", []),
        "cagr10Y": m.get("cagr"),
        "roicProxy10Y": m.get("roicProxy"),
        "alphaVsSP500": m.get("alphaVsBenchmark"),
        "sharpe10Y": m.get("sharpe"),
        "maxDrawdown10Y": m.get("maxDrawdown"),
        "latest13FValueUsd": scored.get("latest13FValueUsd"),
        "latestReportPeriod": scored.get("latestReportPeriod"),
        "topSector": scored.get("topSector"),
        "topSectorWeight": scored.get("topSectorWeight"),
        "top10HoldingsWeight": scored.get("top10HoldingsWeight"),
        "dataConfidenceScore": conf.get("score"),
        "dataConfidenceLabel": conf.get("label"),
        "lastFilingDate": scored.get("lastFilingDate"),
        "leaderboardScore": scored.get("leaderboard_score"),
    }


async def run_fund_leaderboard_job(
    universe_size: int = DEFAULT_UNIVERSE_SIZE,
    top_n: int = DEFAULT_TOP_N,
    max_quarters: int = DEFAULT_MAX_QUARTERS,
    discovery_max: Optional[int] = None,
) -> Dict[str, Any]:
    """Run the full pipeline and persist a fresh leaderboard snapshot."""
    from .coral_skills.sec_universe import discover_top_filers
    from .coral_skills.leaderboard_scoring import rank_leaderboard

    store.init_schema()
    _run_state.update({"status": "running", "started_at": datetime.utcnow().isoformat(), "summary": None})
    summary: Dict[str, Any] = {
        "universe_size": universe_size,
        "top_n": top_n,
        "filers_discovered": 0,
        "managers_scored": 0,
        "leaderboard_rows": 0,
        "errors": [],
    }

    try:
        filers = await discover_top_filers(universe_size=universe_size, discovery_max=discovery_max)
        summary["filers_discovered"] = len(filers)
        if not filers:
            _run_state.update({"status": "error", "summary": summary})
            return summary

        scored: List[Dict[str, Any]] = []
        for filer in filers:
            try:
                result = await _process_manager(filer, max_quarters)
                if result:
                    scored.append(result)
            except Exception as e:
                summary["errors"].append(f"{filer.get('cik')}: {e}")
                logger.warning("[FundLB] manager %s failed: %s", filer.get("cik"), e)

        summary["managers_scored"] = len(scored)
        if not scored:
            _run_state.update({"status": "error", "summary": summary})
            return summary

        ranked = rank_leaderboard(scored)
        top = ranked[:top_n]
        rows = [_to_presentable_row(f) for f in top]

        as_of = date.today().isoformat()
        latest_period = max(
            (r.get("latestReportPeriod") for r in rows if r.get("latestReportPeriod")),
            default=None,
        )
        store.write_leaderboard_snapshot(as_of, latest_period, MODE, rows)
        summary["leaderboard_rows"] = len(rows)

        _run_state.update({"status": "completed", "summary": summary})
        logger.info("[FundLB] job complete: %s", summary)
        return summary
    except Exception as e:
        summary["errors"].append(str(e))
        _run_state.update({"status": "error", "summary": summary})
        logger.exception("[FundLB] job failed: %s", e)
        return summary


def run_job_blocking(**kwargs) -> Dict[str, Any]:
    """Synchronous entrypoint for schedulers/threads."""
    return asyncio.run(run_fund_leaderboard_job(**kwargs))
