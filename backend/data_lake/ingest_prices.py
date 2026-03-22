"""
Phase 2 — Ingest 15 years of daily OHLCV prices for all S&P 500 stocks.

Uses yfinance batch download (up to 50 tickers per call) for efficiency.
Computes derived columns: daily_return_pct, ma_20, ma_50, ma_200, relative_volume.
Stores one Parquet file per ticker.

Usage:
    python -m backend.data_lake.ingest_prices --dry-run
    python -m backend.data_lake.ingest_prices --tickers AAPL,MSFT
    python -m backend.data_lake.ingest_prices
"""
import argparse
import logging
import time
import os

import pandas as pd
import yfinance as yf

from . import config
from . import checkpoint

logger = logging.getLogger(__name__)
PHASE = "prices"


def _compute_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicators to a single-ticker OHLCV dataframe."""
    if df.empty:
        return df
    df = df.copy()
    df["daily_return_pct"] = df["Close"].pct_change() * 100
    df["ma_20"] = df["Close"].rolling(20).mean()
    df["ma_50"] = df["Close"].rolling(50).mean()
    df["ma_200"] = df["Close"].rolling(200).mean()
    vol_avg = df["Volume"].rolling(20).mean()
    df["relative_volume"] = (df["Volume"] / vol_avg).round(2)
    return df


def _yf_symbols(tickers: list[str]) -> tuple[list[str], dict[str, str]]:
    """Map canonical tickers to Yahoo symbols; yf column name -> canonical key."""
    yf_list: list[str] = []
    yf_to_canon: dict[str, str] = {}
    for t in tickers:
        yf = config.YFINANCE_SYMBOL_ALIASES.get(t, t)
        yf_list.append(yf)
        yf_to_canon[yf] = t
    return yf_list, yf_to_canon


def ingest_batch(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Download OHLCV for a batch of tickers. Returns {canonical_ticker: dataframe}."""
    yf_tickers, yf_to_canon = _yf_symbols(tickers)
    logger.info("Downloading batch of %d tickers: %s ... %s", len(tickers), tickers[0], tickers[-1])
    try:
        raw = yf.download(
            yf_tickers,
            start=start,
            end=end,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
        )
    except Exception as e:
        logger.error("yf.download failed for batch: %s", e)
        return {}

    results = {}
    if len(yf_tickers) == 1:
        yf_t = yf_tickers[0]
        canon = yf_to_canon[yf_t]
        if not raw.empty:
            results[canon] = _compute_derived(raw)
    else:
        for yf_t in yf_tickers:
            canon = yf_to_canon[yf_t]
            try:
                df = raw[yf_t].dropna(how="all")
                if not df.empty:
                    results[canon] = _compute_derived(df)
            except (KeyError, Exception) as e:
                logger.warning("No data for %s (yf=%s): %s", canon, yf_t, e)
    return results


def run(tickers: list[str], dry_run: bool = False) -> dict:
    """Main entry point for price ingestion."""
    config.ensure_dirs()
    remaining = checkpoint.get_remaining(PHASE, tickers)
    logger.info("[Prices] %d tickers total, %d remaining", len(tickers), len(remaining))

    if dry_run:
        logger.info("[DRY RUN] Would download prices for %d tickers", len(remaining))
        logger.info("[DRY RUN] Sample: %s", remaining[:5])
        return {"phase": PHASE, "total": len(remaining), "ingested": 0, "dry_run": True}

    ingested = 0
    errors = []
    batch_size = config.YFINANCE_BATCH_SIZE

    for i in range(0, len(remaining), batch_size):
        batch = remaining[i : i + batch_size]
        results = ingest_batch(batch, config.START_DATE, config.END_DATE)

        for ticker, df in results.items():
            out_path = os.path.join(config.PRICES_DIR, f"{ticker}.parquet")
            df.to_parquet(out_path, index=True)
            checkpoint.mark_done(PHASE, ticker)
            ingested += 1

        failed = set(batch) - set(results.keys())
        for t in failed:
            errors.append(t)
            # Do not mark_done — allows retry after symbol fix or data availability

        if i + batch_size < len(remaining):
            logger.info("Sleeping %ds between batches...", config.YFINANCE_SLEEP_BETWEEN_BATCHES)
            time.sleep(config.YFINANCE_SLEEP_BETWEEN_BATCHES)

    logger.info("[Prices] Done: %d ingested, %d errors", ingested, len(errors))
    return {"phase": PHASE, "ingested": ingested, "errors": errors}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest daily OHLCV prices")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tickers", type=str, default=None, help="Comma-separated tickers")
    args = parser.parse_args()

    ticker_list = config.get_tickers(args.tickers, args.dry_run)
    run(ticker_list, dry_run=args.dry_run)
