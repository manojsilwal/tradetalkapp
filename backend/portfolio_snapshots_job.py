"""
Nightly portfolio snapshot job — Your Morning v0 Phase 2.

Computes per-user daily portfolio snapshots, upserts portfolio_snapshots,
detects milestone events, and writes portfolio_reaction_memory on big moves.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import paper_portfolio as pp
from . import portfolio_memory as pm
from .portfolio_holdings_reconcile import aggregate_open_long_positions
from .progress_db import resolve_progress_db_path

logger = logging.getLogger(__name__)

DB_PATH = resolve_progress_db_path()
_local = threading.local()

SPY_TICKER = "SPY"
QQQ_TICKER = "QQQ"

BIG_MOVE_PCT = 3.0
BIG_IMPACT_PCT = 0.25
SECTOR_SHIFT_PCT = 5.0
GAIN_MILESTONES = (10.0, 25.0, 50.0, 100.0)
LOSS_MILESTONES = (-5.0, -10.0, -20.0, -30.0)


def _use_postgres() -> bool:
    try:
        from .postgres_config import postgres_enabled

        return postgres_enabled()
    except Exception:
        return False


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def _pg_connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from .postgres_config import postgres_dsn

    return psycopg2.connect(postgres_dsn(), cursor_factory=RealDictCursor)


def list_users_with_open_positions() -> List[str]:
    """Distinct user_ids with at least one open position."""
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT user_id FROM paper_positions WHERE closed = 0"
                )
                rows = cur.fetchall()
            conn.close()
            return [r["user_id"] for r in rows if r.get("user_id")]

        conn = _get_conn()
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM paper_positions WHERE closed = 0"
        ).fetchall()
        return [r["user_id"] for r in rows]
    except Exception as exc:
        logger.warning("[portfolio_snapshots] list_users failed: %s", exc)
        return []


def _benchmark_close_on_date(ticker: str, trade_date: date) -> Optional[float]:
    try:
        import yfinance as yf

        start = trade_date - timedelta(days=10)
        end = trade_date + timedelta(days=1)
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(), end=end.isoformat(), auto_adjust=True
        )
        if hist is None or hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception:
        return None


def _benchmark_daily_return_pct(ticker: str, trade_date: date) -> Optional[float]:
    """Session return % for an index on trade_date vs prior close."""
    try:
        import yfinance as yf

        start = trade_date - timedelta(days=10)
        end = trade_date + timedelta(days=1)
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(), end=end.isoformat(), auto_adjust=True
        )
        if hist is None or hist.empty:
            return None
        hist = hist.sort_index()
        dates = [d.date() if hasattr(d, "date") else d for d in hist.index]
        if trade_date not in dates:
            # Weekend/holiday — use last available row as proxy
            if len(hist) < 2:
                return None
            prev_close = float(hist["Close"].iloc[-2])
            last_close = float(hist["Close"].iloc[-1])
        else:
            idx = dates.index(trade_date)
            if idx < 1:
                return None
            prev_close = float(hist["Close"].iloc[idx - 1])
            last_close = float(hist["Close"].iloc[idx])
        if prev_close <= 0:
            return None
        return round((last_close - prev_close) / prev_close * 100, 4)
    except Exception:
        return None


def _position_daily_return_pct(
    ticker: str,
    trade_date: date,
    fallback_price: float,
) -> Optional[float]:
    """Daily % move for a symbol; falls back to 0 when history missing."""
    try:
        import yfinance as yf

        start = trade_date - timedelta(days=10)
        end = trade_date + timedelta(days=1)
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(), end=end.isoformat(), auto_adjust=True
        )
        if hist is None or hist.empty or len(hist) < 2:
            return 0.0
        prev_close = float(hist["Close"].iloc[-2])
        last_close = float(hist["Close"].iloc[-1])
        if prev_close <= 0:
            return 0.0
        return round((last_close - prev_close) / prev_close * 100, 4)
    except Exception:
        return 0.0


def _cumulative_return_since_entry(entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((current_price - entry_price) / entry_price * 100, 4)


def calculate_snapshot_for_user(
    user_id: str,
    snapshot_date: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build a snapshot payload for one user. Does not persist.
    Returns None when user has no open positions.
    """
    snap_date = snapshot_date or date.today()
    positions = pp.get_positions(user_id, include_closed=False)
    if not positions:
        return None

    perf = pp.get_portfolio_performance(user_id, use_cache=False)
    enriched = perf.get("positions") or []
    if not enriched:
        return None

    total_value = float(perf.get("total_value") or 0)
    total_cost = sum(float(p.get("allocated") or 0) for p in enriched)
    cumulative_return_pct = (
        round((total_value - total_cost) / total_cost * 100, 4) if total_cost > 0 else 0.0
    )

    agg = aggregate_open_long_positions(positions)
    position_rows: List[Dict[str, Any]] = []
    daily_impact_total = 0.0

    for p in enriched:
        ticker = p["ticker"]
        cur = float(p.get("current_price") or p.get("entry_price") or 0)
        pos_value = float(p.get("current_value") or 0)
        weight = (pos_value / total_value) if total_value > 0 else 0.0
        daily_ret = _position_daily_return_pct(ticker, snap_date, cur)
        impact_pct = round(weight * daily_ret, 4)
        daily_impact_total += impact_pct
        entry = float(p.get("entry_price") or 0)
        position_rows.append({
            "symbol": ticker,
            "quantity": float(p.get("shares") or 0),
            "market_value": round(pos_value, 2),
            "portfolio_weight": round(weight, 4),
            "daily_return_pct": daily_ret,
            "cumulative_return_since_entry_pct": _cumulative_return_since_entry(entry, cur),
            "entry_date": p.get("entry_date"),
            "sector": p.get("sector") or "Unknown",
        })

    position_rows.sort(key=lambda x: x["market_value"], reverse=True)
    top = position_rows[0] if position_rows else {}
    by_sector = perf.get("analysis", {}).get("by_sector") or {}
    sector_exposures = {
        k: round(v / total_value, 4) if total_value > 0 else 0.0
        for k, v in by_sector.items()
    }

    prev = _get_snapshot_for_date(user_id, snap_date - timedelta(days=1))
    daily_return_pct = round(daily_impact_total, 4)
    daily_return_value = None
    if prev and prev.get("portfolio_value"):
        prev_val = float(prev["portfolio_value"])
        if prev_val > 0:
            daily_return_value = round(total_value - prev_val, 2)
            daily_return_pct = round((total_value - prev_val) / prev_val * 100, 4)

    spy_ret = _benchmark_daily_return_pct(SPY_TICKER, snap_date)
    qqq_ret = _benchmark_daily_return_pct(QQQ_TICKER, snap_date)
    spy_close = _benchmark_close_on_date(SPY_TICKER, snap_date)

    return {
        "user_id": user_id,
        "snapshot_date": snap_date.isoformat(),
        "portfolio_value": round(total_value, 2),
        "spy_value": spy_close if spy_close is not None else 0.0,
        "spy_return_pct": spy_ret,
        "positions_json": position_rows,
        "recorded_at": time.time(),
        "daily_return_pct": daily_return_pct,
        "daily_return_value": daily_return_value,
        "cumulative_return_pct": cumulative_return_pct,
        "qqq_return_pct": qqq_ret,
        "top_position_symbol": top.get("symbol"),
        "top_position_weight": top.get("portfolio_weight"),
        "sector_exposures": sector_exposures,
        "_aggregated": agg,
    }


def _get_snapshot_for_date(user_id: str, d: date) -> Optional[Dict[str, Any]]:
    iso = d.isoformat()
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM portfolio_snapshots
                    WHERE user_id = %s AND snapshot_date = %s
                    """,
                    (user_id, iso),
                )
                row = cur.fetchone()
            conn.close()
            return dict(row) if row else None

        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE user_id=? AND snapshot_date=?",
            (user_id, iso),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def upsert_snapshot(payload: Dict[str, Any]) -> bool:
    """Idempotent upsert on (user_id, snapshot_date)."""
    if not payload.get("user_id") or not payload.get("snapshot_date"):
        return False
    positions_json = json.dumps(payload.get("positions_json") or [])
    sector_json = json.dumps(payload.get("sector_exposures") or {})
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolio_snapshots
                    (user_id, snapshot_date, portfolio_value, spy_value, positions_json,
                     recorded_at, daily_return_pct, daily_return_value, cumulative_return_pct,
                     qqq_return_pct, top_position_symbol, top_position_weight, sector_exposures,
                     spy_return_pct)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id, snapshot_date) DO UPDATE SET
                        portfolio_value = EXCLUDED.portfolio_value,
                        spy_value = EXCLUDED.spy_value,
                        positions_json = EXCLUDED.positions_json,
                        recorded_at = EXCLUDED.recorded_at,
                        daily_return_pct = EXCLUDED.daily_return_pct,
                        daily_return_value = EXCLUDED.daily_return_value,
                        cumulative_return_pct = EXCLUDED.cumulative_return_pct,
                        qqq_return_pct = EXCLUDED.qqq_return_pct,
                        top_position_symbol = EXCLUDED.top_position_symbol,
                        top_position_weight = EXCLUDED.top_position_weight,
                        sector_exposures = EXCLUDED.sector_exposures,
                        spy_return_pct = EXCLUDED.spy_return_pct
                    """,
                    (
                        payload["user_id"],
                        payload["snapshot_date"],
                        payload["portfolio_value"],
                        payload.get("spy_value") or 0.0,
                        positions_json,
                        payload["recorded_at"],
                        payload.get("daily_return_pct"),
                        payload.get("daily_return_value"),
                        payload.get("cumulative_return_pct"),
                        payload.get("qqq_return_pct"),
                        payload.get("top_position_symbol"),
                        payload.get("top_position_weight"),
                        sector_json,
                        payload.get("spy_return_pct"),
                    ),
                )
            conn.commit()
            conn.close()
        else:
            conn = _get_conn()
            conn.execute(
                """
                INSERT INTO portfolio_snapshots
                (user_id, snapshot_date, portfolio_value, spy_value, positions_json,
                 recorded_at, daily_return_pct, daily_return_value, cumulative_return_pct,
                 qqq_return_pct, top_position_symbol, top_position_weight, sector_exposures,
                 spy_return_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, snapshot_date) DO UPDATE SET
                    portfolio_value = excluded.portfolio_value,
                    spy_value = excluded.spy_value,
                    positions_json = excluded.positions_json,
                    recorded_at = excluded.recorded_at,
                    daily_return_pct = excluded.daily_return_pct,
                    daily_return_value = excluded.daily_return_value,
                    cumulative_return_pct = excluded.cumulative_return_pct,
                    qqq_return_pct = excluded.qqq_return_pct,
                    top_position_symbol = excluded.top_position_symbol,
                    top_position_weight = excluded.top_position_weight,
                    sector_exposures = excluded.sector_exposures,
                    spy_return_pct = excluded.spy_return_pct
                """,
                (
                    payload["user_id"],
                    payload["snapshot_date"],
                    payload["portfolio_value"],
                    payload.get("spy_value") or 0.0,
                    positions_json,
                    payload["recorded_at"],
                    payload.get("daily_return_pct"),
                    payload.get("daily_return_value"),
                    payload.get("cumulative_return_pct"),
                    payload.get("qqq_return_pct"),
                    payload.get("top_position_symbol"),
                    payload.get("top_position_weight"),
                    sector_json,
                    payload.get("spy_return_pct"),
                ),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("[portfolio_snapshots] upsert failed user=%s: %s", payload.get("user_id"), exc)
        return False


def _crossed_milestone(prev: Optional[float], cur: float, milestones: Tuple[float, ...]) -> Optional[float]:
    if prev is None:
        return None
    for m in sorted(milestones, key=abs, reverse=True):
        if prev < m <= cur or prev > m >= cur:
            return m
    return None


def detect_events_from_snapshot(
    payload: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
) -> List[str]:
    """Emit portfolio_events for meaningful changes. Returns event types logged."""
    user_id = payload["user_id"]
    snap_date = payload["snapshot_date"]
    logged: List[str] = []

    prev_top = (previous or {}).get("top_position_symbol")
    cur_top = payload.get("top_position_symbol")
    if cur_top and cur_top != prev_top:
        pm.log_portfolio_event(
            user_id,
            "position_became_top_holding",
            symbol=cur_top,
            title=f"{cur_top} is now your largest holding",
            event_date=snap_date,
        )
        logged.append("position_became_top_holding")

    prev_sectors: Dict[str, float] = {}
    if previous and previous.get("sector_exposures"):
        raw = previous["sector_exposures"]
        prev_sectors = json.loads(raw) if isinstance(raw, str) else dict(raw)
    cur_sectors = payload.get("sector_exposures") or {}
    for sector, weight in cur_sectors.items():
        prev_w = float(prev_sectors.get(sector) or 0)
        if abs(weight - prev_w) * 100 >= SECTOR_SHIFT_PCT:
            pm.log_portfolio_event(
                user_id,
                "sector_exposure_changed",
                title=f"{sector} exposure shifted",
                description=f"{sector} moved from {prev_w*100:.1f}% to {weight*100:.1f}% of your portfolio.",
                metadata={"sector": sector, "prev_weight": prev_w, "new_weight": weight},
                event_date=snap_date,
            )
            logged.append("sector_exposure_changed")

    prev_positions: Dict[str, Dict[str, Any]] = {}
    if previous and previous.get("positions_json"):
        raw = previous["positions_json"]
        rows = json.loads(raw) if isinstance(raw, str) else raw
        for row in rows or []:
            prev_positions[row.get("symbol", "")] = row

    for pos in payload.get("positions_json") or []:
        sym = pos.get("symbol")
        if not sym:
            continue
        daily_ret = float(pos.get("daily_return_pct") or 0)
        weight = float(pos.get("portfolio_weight") or 0)
        impact = abs(weight * daily_ret)
        cum = float(pos.get("cumulative_return_since_entry_pct") or 0)
        prev_cum = float((prev_positions.get(sym) or {}).get("cumulative_return_since_entry_pct") or 0)

        if abs(daily_ret) >= BIG_MOVE_PCT and impact >= BIG_IMPACT_PCT:
            pm.log_portfolio_event(
                user_id,
                "big_move_for_held_position",
                symbol=sym,
                title=f"{sym} had a big move",
                description=f"{sym} moved {daily_ret:+.1f}% today.",
                metadata={"move_pct": daily_ret, "portfolio_impact_pct": round(weight * daily_ret, 4)},
                event_date=snap_date,
            )
            pm.upsert_portfolio_reaction(
                user_id,
                sym,
                snap_date,
                move_pct=daily_ret,
                portfolio_impact_pct=round(weight * daily_ret, 4),
                portfolio_impact_value=round(
                    float(payload.get("portfolio_value") or 0) * weight * daily_ret / 100, 2
                ),
                metadata={"weight": weight},
            )
            logged.append("big_move_for_held_position")

        gain_cross = _crossed_milestone(prev_cum, cum, GAIN_MILESTONES)
        if gain_cross is not None and gain_cross > 0:
            pm.log_portfolio_event(
                user_id,
                "position_crossed_gain_threshold",
                symbol=sym,
                title=f"{sym} crossed +{gain_cross:.0f}% since entry",
                event_date=snap_date,
                metadata={"milestone_pct": gain_cross, "cumulative_return_pct": cum},
            )
            logged.append("position_crossed_gain_threshold")

        loss_cross = _crossed_milestone(prev_cum, cum, LOSS_MILESTONES)
        if loss_cross is not None and loss_cross < 0:
            pm.log_portfolio_event(
                user_id,
                "position_crossed_loss_threshold",
                symbol=sym,
                title=f"{sym} crossed {loss_cross:.0f}% since entry",
                event_date=snap_date,
                metadata={"milestone_pct": loss_cross, "cumulative_return_pct": cum},
            )
            logged.append("position_crossed_loss_threshold")

    return logged


def write_portfolio_snapshots(
    snapshot_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Run snapshot job for all users with open positions.
    Idempotent per (user_id, snapshot_date).
    """
    snap_date = snapshot_date or date.today()
    summary: Dict[str, Any] = {
        "snapshot_date": snap_date.isoformat(),
        "users_processed": 0,
        "snapshots_written": 0,
        "events_logged": 0,
        "errors": [],
    }
    user_ids = list_users_with_open_positions()
    for uid in user_ids:
        summary["users_processed"] += 1
        try:
            payload = calculate_snapshot_for_user(uid, snap_date)
            if not payload:
                continue
            prev = _get_snapshot_for_date(uid, snap_date - timedelta(days=1))
            if upsert_snapshot(payload):
                summary["snapshots_written"] += 1
                events = detect_events_from_snapshot(payload, prev)
                summary["events_logged"] += len(events)
        except Exception as exc:
            msg = f"{uid}: {exc}"
            summary["errors"].append(msg)
            logger.warning("[portfolio_snapshots] %s", msg)
    logger.info("[portfolio_snapshots] job complete: %s", summary)
    return summary


async def run_portfolio_snapshots_job(snapshot_date: Optional[date] = None) -> Dict[str, Any]:
    """Async wrapper for scheduler / cron."""
    import asyncio

    return await asyncio.to_thread(write_portfolio_snapshots, snapshot_date)
