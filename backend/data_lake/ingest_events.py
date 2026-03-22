"""
Phase 4 — Ingest earnings events, corporate actions, insider trades, holders, analyst recs.

For each ticker, fetches (yfinance):
  4a. Earnings dates: EPS actual vs estimate; revenue actual (+ estimate when available)
  4b. Stock splits, dividends
  4c. Insider transactions (normalized columns)
  4d. Institutional holders, major holders (% breakdown)
  4e. Analyst recommendations (firm / grade / action / date when available)

Storage (preferred): data_lake_output/events/{TICKER}_{kind}.parquet
Legacy nested dirs are still read by summarize_for_rag via config.resolve_event_parquet.

Usage:
    python -m backend.data_lake.ingest_events --dry-run
    python -m backend.data_lake.ingest_events --tickers AAPL,MSFT
    python -m backend.data_lake.ingest_events --kinds insider,recommendations --tickers AAPL
"""
from __future__ import annotations

import argparse
import logging
import time
import os

import pandas as pd
import yfinance as yf

from . import config
from . import checkpoint

logger = logging.getLogger(__name__)
PHASE = "events"

DEFAULT_KINDS = (
    "earnings",
    "splits",
    "dividends",
    "insider",
    "institutional",
    "major_holders",
    "recommendations",
)


def _safe_fetch(func, label: str) -> pd.DataFrame:
    """Call a yfinance property safely, returning empty DataFrame on failure."""
    try:
        result = func()
        if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
            if isinstance(result, pd.Series):
                return result.to_frame()
            return result
    except Exception as e:
        logger.debug("[%s] not available: %s", label, e)
    return pd.DataFrame()


def _normalize_insider_df(df: pd.DataFrame) -> pd.DataFrame:
    """Map yfinance insider_transactions columns to: date, insider_name, shares, value_usd, transaction_type."""
    if df.empty:
        return df
    out = df.copy()
    colmap = {}
    for c in out.columns:
        cl = str(c).lower().replace(" ", "_")
        if "start" in cl and "date" in cl:
            colmap[c] = "date"
        elif cl == "insider" or "insider" in cl and "name" not in colmap.values():
            colmap[c] = "insider_name"
        elif "share" in cl:
            colmap[c] = "shares"
        elif "value" in cl:
            colmap[c] = "value_usd"
        elif "transaction" in cl or cl == "position" or "text" in cl:
            if "transaction_type" not in colmap.values():
                colmap[c] = "transaction_type"
    ren = {k: v for k, v in colmap.items()}
    if ren:
        out = out.rename(columns=ren)
    # Ensure expected columns exist
    for c in ("date", "insider_name", "shares", "value_usd", "transaction_type"):
        if c not in out.columns:
            out[c] = pd.NA
    return out


def _normalize_recommendations_df(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns if present; keep firm / grade / action / date when possible."""
    if df.empty:
        return df
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = ["_".join(str(x) for x in tup if str(x) != "nan").strip("_") for tup in out.columns]
    out.columns = [str(c).replace(" ", "_") for c in out.columns]
    return out


def _enrich_earnings_with_revenue(stock: yf.Ticker, earnings_dates: pd.DataFrame) -> pd.DataFrame:
    """
    Merge quarterly revenue (and earnings) from quarterly_earnings into earnings_dates index.
    yfinance earnings_dates: EPS Estimate, Reported EPS (and sometimes Revenue Estimate / Reported Revenue).
    """
    if earnings_dates.empty:
        return earnings_dates
    ed = earnings_dates.copy()
    try:
        qe = stock.quarterly_earnings
        if qe is None or qe.empty:
            return ed
        # qe index = period end dates; columns often Revenue, Earnings
        qe = qe.copy()
        qe.index = pd.to_datetime(qe.index).tz_localize(None)
        ed.index = pd.to_datetime(ed.index).tz_localize(None)

        rev_col = "Revenue" if "Revenue" in qe.columns else None
        earn_col = "Earnings" if "Earnings" in qe.columns else None

        rev_map: dict = {}
        earn_map: dict = {}
        for dt, row in qe.iterrows():
            if rev_col and not pd.isna(row.get(rev_col)):
                rev_map[dt.normalize()] = float(row[rev_col])
            if earn_col and not pd.isna(row.get(earn_col)):
                earn_map[dt.normalize()] = float(row[earn_col])

        reported_rev = []
        for idx in ed.index:
            k = pd.Timestamp(idx).normalize()
            # nearest quarter end in qe
            best = None
            best_diff = None
            for qd in qe.index:
                d = pd.Timestamp(qd).normalize()
                diff = abs((k - d).days)
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = d
            if best is not None and best_diff is not None and best_diff <= 45:
                reported_rev.append(rev_map.get(best))
            else:
                reported_rev.append(None)

        if "Reported Revenue" not in ed.columns:
            ed["Reported Revenue"] = reported_rev
        else:
            ed["Reported Revenue"] = ed["Reported Revenue"].fillna(reported_rev)

        # Revenue estimate rarely in free API — leave column for downstream if present on ed
        if "Revenue Estimate" not in ed.columns:
            ed["Revenue Estimate"] = pd.NA
    except Exception as e:
        logger.debug("Revenue enrich skipped: %s", e)
    return ed


def ingest_ticker(ticker: str, kinds: tuple[str, ...] | None = None) -> dict[str, pd.DataFrame]:
    """Fetch event data for a single ticker. kinds=None fetches all supported types."""
    stock = yf.Ticker(config.yfinance_symbol(ticker))
    want = set(kinds) if kinds else set(DEFAULT_KINDS)
    results: dict[str, pd.DataFrame] = {}

    if "earnings" in want:
        earnings = _safe_fetch(lambda: stock.earnings_dates, f"{ticker}/earnings_dates")
        if not earnings.empty:
            earnings = _enrich_earnings_with_revenue(stock, earnings)
            results["earnings"] = earnings

    if "splits" in want:
        splits = _safe_fetch(lambda: stock.splits, f"{ticker}/splits")
        if not splits.empty:
            results["splits"] = splits

    if "dividends" in want:
        dividends = _safe_fetch(lambda: stock.dividends, f"{ticker}/dividends")
        if not dividends.empty:
            results["dividends"] = dividends

    if "insider" in want:
        insider = _safe_fetch(lambda: stock.insider_transactions, f"{ticker}/insider")
        if not insider.empty:
            results["insider"] = _normalize_insider_df(insider)

    if "institutional" in want:
        institutional = _safe_fetch(lambda: stock.institutional_holders, f"{ticker}/institutional")
        if not institutional.empty:
            results["institutional"] = institutional

    if "major_holders" in want:
        major = _safe_fetch(lambda: stock.major_holders, f"{ticker}/major_holders")
        if not major.empty:
            results["major_holders"] = major

    if "recommendations" in want:
        recs = _safe_fetch(lambda: stock.recommendations, f"{ticker}/recommendations")
        if not recs.empty:
            results["recommendations"] = _normalize_recommendations_df(recs)

    return results


def _write_ticker_events(ticker: str, data: dict[str, pd.DataFrame]) -> int:
    """Write parquet files (flat layout). Returns file count."""
    n = 0
    for dtype, df in data.items():
        out_path = config.event_parquet_path(ticker, dtype)
        df.to_parquet(out_path, index=True)
        n += 1
    return n


def run(
    tickers: list[str],
    dry_run: bool = False,
    kinds: tuple[str, ...] | None = None,
    ignore_checkpoint: bool = False,
) -> dict:
    """Main entry point for events ingestion."""
    config.ensure_dirs()
    if ignore_checkpoint:
        remaining = list(tickers)
    else:
        remaining = checkpoint.get_remaining(PHASE, tickers)
    logger.info("[Events] %d tickers total, %d remaining", len(tickers), len(remaining))

    if dry_run:
        logger.info("[DRY RUN] Would fetch events for %d tickers", len(remaining))
        return {"phase": PHASE, "total": len(remaining), "ingested": 0, "dry_run": True}

    ingested = 0
    total_files = 0
    errors = []

    full_run = kinds is None or set(kinds) == set(DEFAULT_KINDS)

    for i, ticker in enumerate(remaining):
        try:
            data = ingest_ticker(ticker, kinds=kinds)
            if data:
                total_files += _write_ticker_events(ticker, data)
                ingested += 1
                if full_run:
                    checkpoint.mark_done(PHASE, ticker)
            else:
                errors.append(ticker)
        except Exception as e:
            logger.warning("[%s] Failed: %s", ticker, e)
            errors.append(ticker)

        if (i + 1) % 50 == 0:
            logger.info("Progress: %d / %d tickers (%d files written)", i + 1, len(remaining), total_files)

        time.sleep(config.YFINANCE_SLEEP_BETWEEN_TICKERS)

    logger.info("[Events] Done: %d tickers, %d files, %d errors", ingested, total_files, len(errors))
    return {"phase": PHASE, "ingested": ingested, "total_files": total_files, "errors": errors}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest events and alternative data")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tickers", type=str, default=None)
    parser.add_argument(
        "--kinds",
        type=str,
        default=None,
        help="Comma subset of: earnings,splits,dividends,insider,institutional,major_holders,recommendations",
    )
    parser.add_argument(
        "--ignore-checkpoint",
        action="store_true",
        help="Process all tickers in --tickers list even if checkpoint says done (for partial refreshes)",
    )
    args = parser.parse_args()

    ticker_list = config.get_tickers(args.tickers, args.dry_run)
    kinds_tuple = None
    if args.kinds:
        kinds_tuple = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    run(
        ticker_list,
        dry_run=args.dry_run,
        kinds=kinds_tuple,
        ignore_checkpoint=args.ignore_checkpoint,
    )
