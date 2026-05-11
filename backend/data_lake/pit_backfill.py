"""
Backfill ``effective_date`` and ``knowledge_date`` on fundamentals parquet files.

Run once after upgrading schemas (offline / batch):

    PYTHONPATH=. python -m backend.data_lake.pit_backfill --tickers AAPL,MSFT
    PYTHONPATH=. python -m backend.data_lake.pit_backfill   # all ALL_TICKERS

For historical rows without filings metadata, uses quarter ``effective_date``
from the index and ``knowledge_date = effective_date + filing_lag_days`` (45).
"""

from __future__ import annotations

import argparse
import logging
import os
from . import config

logger = logging.getLogger(__name__)

DEFAULT_LAG = int(os.environ.get("PREDICTOR_PIT_FILING_LAG_DAYS", "45"))


def enrich_dataframe(df):
    """Return copy with ``effective_date`` and ``knowledge_date`` columns."""
    import pandas as pd

    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        eff = pd.to_datetime(out.index).tz_localize(None)
    else:
        eff = pd.to_datetime(out.index)
    out["effective_date"] = eff
    if "knowledge_date" not in out.columns:
        out["knowledge_date"] = eff + pd.to_timedelta(DEFAULT_LAG, unit="d")
    return out


def backfill_ticker(ticker: str, *, dry_run: bool = False) -> bool:
    import pandas as pd

    path = os.path.join(config.FUNDAMENTALS_DIR, f"{ticker.upper()}.parquet")
    if not os.path.isfile(path):
        logger.warning("[pit_backfill] missing %s", path)
        return False
    df = pd.read_parquet(path)
    enriched = enrich_dataframe(df)
    if dry_run:
        logger.info("[pit_backfill] %s columns=%s", ticker, list(enriched.columns))
        return True
    idx_flag = isinstance(df.index, pd.DatetimeIndex)
    enriched.to_parquet(path, index=idx_flag)
    logger.info("[pit_backfill] wrote %s", path)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default="", help="Comma-separated; empty = ALL_TICKERS")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    if args.tickers.strip():
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = config.ALL_TICKERS

    ok = 0
    for t in tickers:
        if backfill_ticker(t, dry_run=args.dry_run):
            ok += 1
    logger.info("[pit_backfill] done %s/%s", ok, len(tickers))


if __name__ == "__main__":
    main()
