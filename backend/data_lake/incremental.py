"""
Phase 7 — Incremental updates for the local Parquet data lake (daily / weekly hooks).

- Append recent OHLCV onto existing per-ticker price files
- Rotate partial event re-fetches (earnings season vs weekly insider/recs)

Called from backend/daily_pipeline.py (non-blocking / best-effort).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from . import config
from .ingest_prices import _compute_derived, ingest_batch
from . import ingest_events

logger = logging.getLogger(__name__)


def _merge_price_history(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if old.empty:
        return _compute_derived(new)
    if new.empty:
        return _compute_derived(old)
    combined = pd.concat([old, new])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return _compute_derived(combined)


def append_recent_ohlcv(
    tickers: list[str] | None = None,
    extra_days: int = 7,
) -> dict:
    """
    For each ticker with an existing parquet file, download the last `extra_days` of bars
    and merge into the local file. Skips tickers with no existing file (full backfill required).
    """
    config.ensure_dirs()
    universe = tickers or config.SP500_TICKERS
    end_d = date.today()
    start_d = end_d - timedelta(days=extra_days + 5)
    start_s = start_d.isoformat()
    end_s = (end_d + timedelta(days=1)).isoformat()

    updated = 0
    skipped = 0
    errors: list[str] = []

    batch_size = min(config.YFINANCE_BATCH_SIZE, 50)
    batch: list[str] = []

    def flush_batch() -> None:
        nonlocal updated, skipped, errors, batch
        if not batch:
            return
        results = ingest_batch(batch, start_s, end_s)
        for t in batch:
            path = os.path.join(config.PRICES_DIR, f"{t}.parquet")
            if not os.path.isfile(path):
                skipped += 1
                continue
            try:
                new_df = results.get(t)
                if new_df is None or new_df.empty:
                    continue
                old_df = pd.read_parquet(path)
                merged = _merge_price_history(old_df, new_df)
                merged.to_parquet(path, index=True)
                updated += 1
            except Exception as e:
                logger.warning("[IncrementalPrices] %s: %s", t, e)
                errors.append(t)
        batch = []

    for t in universe:
        batch.append(t)
        if len(batch) >= batch_size:
            flush_batch()
            time.sleep(config.YFINANCE_SLEEP_BETWEEN_BATCHES)
    flush_batch()

    logger.info(
        "[IncrementalPrices] updated=%d skipped_no_file=%d errors=%d",
        updated,
        skipped,
        len(errors),
    )
    return {"prices_updated": updated, "prices_skipped": skipped, "errors": errors}


def is_earnings_season_month(d: date | None = None) -> bool:
    """Broad earnings windows: Jan, Apr, Jul, Oct (+ spill months)."""
    m = (d or date.today()).month
    return m in (1, 2, 3, 4, 5, 7, 8, 10, 11)


def rotating_batch(tickers: list[str], day_index: int, batch_size: int) -> list[str]:
    """Stable rotation so each day touches a different slice."""
    if not tickers:
        return []
    n = len(tickers)
    start = (day_index * batch_size) % n
    out = []
    for i in range(min(batch_size, n)):
        out.append(tickers[(start + i) % n])
    return out


def run_daily_event_slice() -> dict:
    """
    Daily: refresh earnings (+ full event bundle) for a rotating batch during earnings season;
    otherwise refresh a smaller earnings-only slice.
    """
    config.ensure_dirs()
    day_key = date.today().toordinal()
    batch_size = 40 if is_earnings_season_month() else 20
    batch = rotating_batch(list(config.SP500_TICKERS), day_key, batch_size)

    if is_earnings_season_month():
        kinds = None  # full event pull for batch
    else:
        kinds = ("earnings",)

    try:
        r = ingest_events.run(batch, dry_run=False, kinds=kinds, ignore_checkpoint=True)
        return {"data_lake_events_batch": len(batch), **r}
    except Exception as e:
        logger.warning("[IncrementalEvents] daily slice failed: %s", e)
        return {"data_lake_events_error": str(e)}


def run_weekly_insider_and_recommendations() -> dict:
    """Weekly (call on Mondays): insider + recommendations for a rotating batch."""
    config.ensure_dirs()
    day_key = date.today().toordinal() // 7
    batch = rotating_batch(list(config.SP500_TICKERS), day_key, 80)
    kinds = ("insider", "recommendations")
    try:
        r = ingest_events.run(batch, dry_run=False, kinds=kinds, ignore_checkpoint=True)
        return {"data_lake_weekly_insider_recs_batch": len(batch), **r}
    except Exception as e:
        logger.warning("[IncrementalEvents] weekly insider/recs failed: %s", e)
        return {"data_lake_weekly_error": str(e)}
