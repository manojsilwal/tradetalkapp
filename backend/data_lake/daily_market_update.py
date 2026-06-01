"""
Daily incremental market update — fetch previous session prices + news/events,
upsert into BigQuery, refresh movement links for new dates only.

Designed to run each weekday morning before US market open (~15–25 min).

Usage:
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.daily_market_update
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.daily_market_update --dry-run
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.daily_market_update --through 2026-05-29
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from . import config
from .ingest_prices import ingest_batch

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 260  # rolling window for MA200 / return pct
LAG_LINK_DAYS = 7


def as_et_today() -> date:
    return datetime.now(ET).date()


def target_through_date() -> date:
    """Last completed calendar day in US/Eastern (yfinance supplies prior session bars)."""
    return as_et_today() - timedelta(days=1)


def get_bq_last_trade_date() -> Optional[date]:
    if _backend_type() != "bigquery":
        return None
    from backend.mcp_server.backend import backend

    rows = backend().query(
        "SELECT MAX(trade_date) AS d FROM daily_prices WHERE close IS NOT NULL"
    )
    if not rows or rows[0].get("d") is None:
        return None
    val = rows[0]["d"]
    if isinstance(val, date):
        return val
    return pd.Timestamp(val).date()


def resolve_ingest_window(
    through: Optional[date] = None,
) -> Optional[Tuple[date, date]]:
    """Return (start, end) trade dates to ingest, or None if already current."""
    through = through or target_through_date()
    last = get_bq_last_trade_date()
    if last is None:
        logger.warning("[DailyMarket] No daily_prices rows — run full backfill first")
        return None
    if last >= through:
        logger.info("[DailyMarket] Already current through %s (BQ max=%s)", through, last)
        return None
    return last + timedelta(days=1), through


def get_active_symbols() -> List[str]:
    """Symbols in BigQuery plus configured universe (covers recent index adds)."""
    symbols: set[str] = set(config.ALL_TICKERS)
    if _backend_type() == "bigquery":
        from backend.mcp_server.backend import backend

        rows = backend().query("SELECT DISTINCT symbol FROM daily_prices")
        symbols.update(r["symbol"] for r in rows if r.get("symbol"))
    return sorted(symbols)


def _backend_type() -> str:
    import os

    return os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()


def _normalize_rows(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    from scripts.sync_prices_to_bq import normalize_price_df

    return normalize_price_df(df, ticker)


def _frame_to_bq_rows(df: pd.DataFrame, ingest_start: date) -> List[Dict]:
    if df.empty or "trade_date" not in df.columns:
        return []
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    mask = out["trade_date"].dt.date >= ingest_start
    out = out.loc[mask]
    if out.empty:
        return []

    rows = []
    ingested_at = datetime.now(timezone.utc).isoformat()
    for _, row in out.iterrows():
        td = row["trade_date"]
        trade_date = td.date() if hasattr(td, "date") else pd.Timestamp(td).date()
        vol = row.get("volume")
        rows.append({
            "symbol": str(row.get("symbol", "")).upper(),
            "trade_date": trade_date.isoformat(),
            "open": _float_or_none(row.get("open")),
            "high": _float_or_none(row.get("high")),
            "low": _float_or_none(row.get("low")),
            "close": _float_or_none(row.get("close")),
            "volume": int(vol) if vol is not None and pd.notna(vol) else None,
            "daily_return_pct": _float_or_none(row.get("daily_return_pct")),
            "ma_20": _float_or_none(row.get("ma_20")),
            "ma_50": _float_or_none(row.get("ma_50")),
            "ma_200": _float_or_none(row.get("ma_200")),
            "relative_volume": _float_or_none(row.get("relative_volume")),
            "ingested_at": ingested_at,
        })
    return rows


def _float_or_none(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def upsert_daily_prices(rows: List[Dict], dry_run: bool = False) -> int:
    if not rows:
        return 0
    dates = sorted({r["trade_date"] for r in rows})
    min_d, max_d = dates[0], dates[-1]

    if dry_run:
        logger.info("[DailyMarket] Would upsert %d price rows (%s → %s)", len(rows), min_d, max_d)
        return len(rows)

    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET

    backend().execute(
        f"DELETE FROM `{FULL_DATASET}.daily_prices` "
        f"WHERE trade_date BETWEEN DATE '{min_d}' AND DATE '{max_d}'"
    )

    batch_size = 5000
    total = 0
    for i in range(0, len(rows), batch_size):
        total += backend().insert_rows("daily_prices", rows[i : i + batch_size])
    logger.info("[DailyMarket] Upserted %d price rows (%s → %s)", total, min_d, max_d)
    return total


def fetch_and_upsert_prices(
    symbols: List[str],
    ingest_start: date,
    ingest_end: date,
    dry_run: bool = False,
) -> Dict:
    fetch_start = ingest_start - timedelta(days=LOOKBACK_DAYS)
    fetch_end = ingest_end + timedelta(days=1)
    start_s = fetch_start.isoformat()
    end_s = fetch_end.isoformat()

    all_rows: List[Dict] = []
    batch_size = min(config.YFINANCE_BATCH_SIZE, 50)
    batch: List[str] = []
    errors: List[str] = []

    def flush() -> None:
        nonlocal batch, all_rows, errors
        if not batch:
            return
        results = ingest_batch(batch, start_s, end_s)
        for sym in batch:
            df = results.get(sym)
            if df is None or df.empty:
                continue
            try:
                norm = _normalize_rows(df, sym)
                all_rows.extend(_frame_to_bq_rows(norm, ingest_start))
            except Exception as exc:
                logger.warning("[DailyMarket] normalize %s: %s", sym, exc)
                errors.append(sym)
        batch = []

    for sym in symbols:
        batch.append(sym)
        if len(batch) >= batch_size:
            flush()
            time.sleep(config.YFINANCE_SLEEP_BETWEEN_BATCHES)
    flush()

    written = upsert_daily_prices(all_rows, dry_run=dry_run)
    return {
        "price_rows": len(all_rows),
        "price_written": written,
        "price_errors": errors,
    }


def upsert_events_curated(events: List[Dict], dry_run: bool = False) -> int:
    if not events:
        return 0
    if dry_run:
        logger.info("[DailyMarket] Would upsert %d events", len(events))
        return len(events)

    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET
    from backend.mcp_server.persist import persist_events_curated

    ids = sorted({e["event_id"] for e in events if e.get("event_id")})
    chunk = 400
    for i in range(0, len(ids), chunk):
        part = ids[i : i + chunk]
        id_sql = ", ".join(f"'{x}'" for x in part)
        backend().execute(
            f"DELETE FROM `{FULL_DATASET}.events_curated` WHERE event_id IN ({id_sql})"
        )

    batch_size = 500
    total = 0
    for i in range(0, len(events), batch_size):
        total += persist_events_curated(events[i : i + batch_size])
    logger.info("[DailyMarket] Upserted %d events", total)
    return total


def append_macro_events(
    start: date,
    end: date,
    dry_run: bool = False,
) -> int:
    from backend.data_lake.ingest_macro_policy import build_macro_events
    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET
    from backend.mcp_server.persist import persist_macro_policy_events

    events = build_macro_events(start=start.isoformat(), end=end.isoformat())
    if not events:
        return 0
    if dry_run:
        logger.info("[DailyMarket] Would append %d macro events", len(events))
        return len(events)

    ids = sorted({e["event_id"] for e in events})
    chunk = 400
    for i in range(0, len(ids), chunk):
        part = ids[i : i + chunk]
        id_sql = ", ".join(f"'{x}'" for x in part)
        backend().execute(
            f"DELETE FROM `{FULL_DATASET}.macro_policy_events` WHERE event_id IN ({id_sql})"
        )
    return persist_macro_policy_events(events)


def fetch_and_upsert_events(
    symbols: List[str],
    ingest_start: date,
    ingest_end: date,
    dry_run: bool = False,
) -> Dict:
    from backend.data_lake.ingest_daily_events import collect_incremental_events

    event_start = ingest_start - timedelta(days=LAG_LINK_DAYS)
    events = collect_incremental_events(event_start, ingest_end, symbols)
    written = upsert_events_curated(events, dry_run=dry_run)
    macro_written = append_macro_events(
        event_start,
        ingest_end + timedelta(days=30),
        dry_run=dry_run,
    )
    return {
        "events_collected": len(events),
        "events_written": written,
        "macro_written": macro_written,
    }


def run_daily_update(
    through: Optional[date] = None,
    dry_run: bool = False,
    skip_links: bool = False,
    skip_features: bool = False,
) -> Dict:
    window = resolve_ingest_window(through=through)
    if window is None:
        through = through or target_through_date()
        last = get_bq_last_trade_date()
        if last and last >= through:
            return {"status": "already_current", "through": through.isoformat()}
        return {"status": "skipped", "reason": "no_bq_baseline"}

    ingest_start, ingest_end = window
    symbols = get_active_symbols()
    logger.info(
        "[DailyMarket] Ingest window %s → %s for %d symbols",
        ingest_start,
        ingest_end,
        len(symbols),
    )

    price_result = fetch_and_upsert_prices(
        symbols, ingest_start, ingest_end, dry_run=dry_run
    )
    event_result = fetch_and_upsert_events(
        symbols, ingest_start, ingest_end, dry_run=dry_run
    )

    link_result: Dict = {"status": "skipped"}
    if not skip_links and not dry_run:
        from backend.mcp_server.build_movement_links import run_incremental_movement_links

        link_start = ingest_start - timedelta(days=LAG_LINK_DAYS)
        link_result = run_incremental_movement_links(
            start_date=link_start.isoformat(),
            end_date=ingest_end.isoformat(),
        )

    feature_result: Dict = {"status": "skipped"}
    if not skip_features and not dry_run:
        from backend.mcp_server.feature_mart import refresh_feature_mart
        from backend.mcp_server.gold_context import refresh_gold_context

        feature_result = {"feature_mart": refresh_feature_mart()}
        feature_result["gold_context"] = refresh_gold_context()

    return {
        "status": "ok",
        "ingest_start": ingest_start.isoformat(),
        "ingest_end": ingest_end.isoformat(),
        "symbols": len(symbols),
        **price_result,
        **event_result,
        "links": link_result,
        "features": feature_result,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Daily incremental market data update")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--through", default=None, help="Override end date YYYY-MM-DD")
    parser.add_argument("--skip-links", action="store_true")
    parser.add_argument("--skip-features", action="store_true")
    args = parser.parse_args()

    through = date.fromisoformat(args.through) if args.through else None
    result = run_daily_update(
        through=through,
        dry_run=args.dry_run,
        skip_links=args.skip_links,
        skip_features=args.skip_features,
    )
    print(result)


if __name__ == "__main__":
    main()
