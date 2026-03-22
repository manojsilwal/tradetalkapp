"""
Phase 4b — Ingest 15 years of macroeconomic indicators from FRED.

Fetches monthly/quarterly series for:
  - Federal Funds Rate (DFF)
  - CPI Year-over-Year (CPIAUCSL)
  - 10-Year Treasury Yield (DGS10)
  - Unemployment Rate (UNRATE)
  - M2 Money Supply (M2SL)
  - GDP Growth (A191RL1Q225SBEA)
  - Consumer Sentiment (UMCSENT)
  - VIX (via yfinance ^VIX)

Stores a single combined Parquet file.

Usage:
    python -m backend.data_lake.ingest_macro --dry-run
    python -m backend.data_lake.ingest_macro
"""
import argparse
import logging
import time
import os

import pandas as pd
import requests
import yfinance as yf

from . import config

logger = logging.getLogger(__name__)

FRED_SERIES = {
    "fed_funds_rate": "DFF",
    "cpi": "CPIAUCSL",
    "treasury_10y": "DGS10",
    "treasury_2y": "DGS2",
    "usd_broad_index": "DTWEXBGS",
    "unemployment": "UNRATE",
    "m2_money_supply": "M2SL",
    "gdp_growth": "A191RL1Q225SBEA",
    "consumer_sentiment": "UMCSENT",
}

FRED_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _fetch_fred_series(series_id: str, start: str, end: str) -> pd.Series:
    """Download a FRED time series as CSV."""
    params = {
        "id": series_id,
        "cosd": start,
        "coed": end,
    }
    try:
        resp = requests.get(FRED_BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        date_col = "observation_date" if "observation_date" in df.columns else "DATE"
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col)
        col = df.columns[0]
        series = pd.to_numeric(df[col], errors="coerce")
        series.name = series_id
        return series.dropna()
    except Exception as e:
        logger.warning("FRED fetch failed for %s: %s", series_id, e)
        return pd.Series(dtype=float)


def _fetch_vix(start: str, end: str) -> pd.Series:
    """Download VIX history from yfinance."""
    try:
        vix = yf.download("^VIX", start=start, end=end, auto_adjust=True)
        if not vix.empty:
            series = vix["Close"]
            if isinstance(series, pd.DataFrame):
                series = series.iloc[:, 0]
            series.name = "VIX"
            return series
    except Exception as e:
        logger.warning("VIX fetch failed: %s", e)
    return pd.Series(dtype=float, name="VIX")


def run(dry_run: bool = False) -> dict:
    """Main entry point for macro ingestion."""
    config.ensure_dirs()
    start = config.START_DATE
    end = config.END_DATE

    if dry_run:
        logger.info("[DRY RUN] Would fetch %d FRED series + VIX from %s to %s",
                     len(FRED_SERIES), start, end)
        return {"phase": "macro", "series": list(FRED_SERIES.keys()) + ["vix"], "dry_run": True}

    all_series = {}

    for name, series_id in FRED_SERIES.items():
        logger.info("Fetching FRED series: %s (%s)", name, series_id)
        s = _fetch_fred_series(series_id, start, end)
        if not s.empty:
            all_series[name] = s
            logger.info("  -> %d data points", len(s))
        time.sleep(config.FRED_SLEEP)

    logger.info("Fetching VIX history...")
    vix = _fetch_vix(start, end)
    if not vix.empty:
        all_series["vix"] = vix
        logger.info("  -> %d data points", len(vix))

    if all_series:
        combined = pd.DataFrame(all_series)
        combined.index.name = "date"
        out_path = os.path.join(config.MACRO_DIR, "macro_indicators.parquet")
        combined.to_parquet(out_path, index=True)
        logger.info("[Macro] Saved %d series to %s", len(all_series), out_path)

        # Also save quarterly snapshots (resampled)
        quarterly = combined.resample("QE").last()
        quarterly_path = os.path.join(config.MACRO_DIR, "macro_quarterly.parquet")
        quarterly.to_parquet(quarterly_path, index=True)
        logger.info("[Macro] Saved quarterly snapshots: %d rows", len(quarterly))

    return {"phase": "macro", "series_count": len(all_series)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest macro indicators from FRED")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
