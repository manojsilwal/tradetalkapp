"""
Portfolio memory — events, user actions, reaction memory, snapshot helpers.

Phase 1 of Your Morning v0: durable per-user context that compounds over time.
All writes are scoped by user_id. Logging failures must never break portfolio UX.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from .progress_db import resolve_progress_db_path

logger = logging.getLogger(__name__)

DB_PATH = resolve_progress_db_path()
_local = threading.local()

_SIZE_CHANGE_THRESHOLD = 0.01  # 1% share change triggers size event


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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj or {}, default=str)


def init_portfolio_memory_db() -> None:
    """Apply progress migrations for portfolio memory tables (idempotent)."""
    if _use_postgres():
        try:
            from pathlib import Path

            mig_dir = Path(__file__).resolve().parent / "migrations" / "postgres"
            conn = _pg_connect()
            with conn.cursor() as cur:
                for name in (
                "001_paper_portfolio.sql",
                "002_portfolio_memory.sql",
                "003_snapshot_spy_return.sql",
            ):
                    ddl = (mig_dir / name).read_text(encoding="utf-8")
                    cur.execute(ddl)
            conn.commit()
            conn.close()
            logger.info("[portfolio_memory] Postgres schema ready")
        except Exception as exc:
            logger.error("[portfolio_memory] Postgres init failed: %s", exc)
        return

    try:
        from .migrations.runner import run_migrations

        run_migrations(DB_PATH, "progress")
        logger.info("[portfolio_memory] SQLite migrations applied")
    except Exception as exc:
        logger.warning("[portfolio_memory] migration skipped: %s", exc)


def log_portfolio_event(
    user_id: str,
    event_type: str,
    *,
    symbol: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    event_date: Optional[str] = None,
) -> Optional[str]:
    """Append a portfolio event. Returns event id or None on failure."""
    if not user_id or not event_type:
        return None
    event_id = _new_id("pevt")
    sym = (symbol or "").strip().upper() or None
    evt_date = event_date or date.today().isoformat()
    meta = _json_dumps(metadata)
    created = time.time()
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolio_events
                    (id, user_id, event_type, symbol, event_date, title, description, metadata, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (event_id, user_id, event_type, sym, evt_date, title, description, meta, created),
                )
            conn.commit()
            conn.close()
        else:
            conn = _get_conn()
            conn.execute(
                """
                INSERT INTO portfolio_events
                (id, user_id, event_type, symbol, event_date, title, description, metadata, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (event_id, user_id, event_type, sym, evt_date, title, description, meta, created),
            )
            conn.commit()
        return event_id
    except Exception as exc:
        logger.warning("[portfolio_memory] log_portfolio_event failed: %s", exc)
        return None


def list_portfolio_events(user_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    """Recent portfolio events for one user (newest first)."""
    if not user_id:
        return []
    limit = max(1, min(int(limit), 100))
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM portfolio_events
                    WHERE user_id = %s
                    ORDER BY event_date DESC, created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
            conn.close()
            return [_row_dict(r) for r in rows]

        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT * FROM portfolio_events
            WHERE user_id = ?
            ORDER BY event_date DESC, created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [_row_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[portfolio_memory] list_portfolio_events failed: %s", exc)
        return []


def log_user_action(
    user_id: str,
    action_type: str,
    *,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    symbol: Optional[str] = None,
    page: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Append an implicit behavioural signal. Returns action id or None on failure."""
    if not user_id or not action_type:
        return None
    action_id = _new_id("uact")
    sym = (symbol or "").strip().upper() or None
    meta = _json_dumps(metadata)
    created = time.time()
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_actions
                    (id, user_id, action_type, entity_type, entity_id, symbol, page, metadata, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        action_id, user_id, action_type, entity_type, entity_id,
                        sym, page, meta, created,
                    ),
                )
            conn.commit()
            conn.close()
        else:
            conn = _get_conn()
            conn.execute(
                """
                INSERT INTO user_actions
                (id, user_id, action_type, entity_type, entity_id, symbol, page, metadata, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    action_id, user_id, action_type, entity_type, entity_id,
                    sym, page, meta, created,
                ),
            )
            conn.commit()
        return action_id
    except Exception as exc:
        logger.warning("[portfolio_memory] log_user_action failed: %s", exc)
        return None


def list_user_actions(user_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
    if not user_id:
        return []
    limit = max(1, min(int(limit), 200))
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM user_actions
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
            conn.close()
            return [_row_dict(r) for r in rows]

        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT * FROM user_actions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [_row_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[portfolio_memory] list_user_actions failed: %s", exc)
        return []


def upsert_portfolio_reaction(
    user_id: str,
    symbol: str,
    event_date: str,
    *,
    move_pct: Optional[float] = None,
    portfolio_impact_value: Optional[float] = None,
    portfolio_impact_pct: Optional[float] = None,
    cause_category: Optional[str] = None,
    one_line_reason: Optional[str] = None,
    source_event_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Idempotent per (user_id, symbol, event_date). Used by nightly snapshot job (phase 2)."""
    if not user_id or not symbol or not event_date:
        return None
    sym = symbol.strip().upper()
    row_id = _new_id("react")
    meta = _json_dumps(metadata)
    created = time.time()
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolio_reaction_memory
                    (id, user_id, symbol, event_date, move_pct, portfolio_impact_value,
                     portfolio_impact_pct, cause_category, one_line_reason, source_event_id,
                     metadata, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id, symbol, event_date) DO UPDATE SET
                        move_pct = EXCLUDED.move_pct,
                        portfolio_impact_value = EXCLUDED.portfolio_impact_value,
                        portfolio_impact_pct = EXCLUDED.portfolio_impact_pct,
                        cause_category = EXCLUDED.cause_category,
                        one_line_reason = EXCLUDED.one_line_reason,
                        source_event_id = EXCLUDED.source_event_id,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        row_id, user_id, sym, event_date, move_pct, portfolio_impact_value,
                        portfolio_impact_pct, cause_category, one_line_reason, source_event_id,
                        meta, created,
                    ),
                )
            conn.commit()
            conn.close()
        else:
            conn = _get_conn()
            conn.execute(
                """
                INSERT INTO portfolio_reaction_memory
                (id, user_id, symbol, event_date, move_pct, portfolio_impact_value,
                 portfolio_impact_pct, cause_category, one_line_reason, source_event_id,
                 metadata, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, symbol, event_date) DO UPDATE SET
                    move_pct = excluded.move_pct,
                    portfolio_impact_value = excluded.portfolio_impact_value,
                    portfolio_impact_pct = excluded.portfolio_impact_pct,
                    cause_category = excluded.cause_category,
                    one_line_reason = excluded.one_line_reason,
                    source_event_id = excluded.source_event_id,
                    metadata = excluded.metadata
                """,
                (
                    row_id, user_id, sym, event_date, move_pct, portfolio_impact_value,
                    portfolio_impact_pct, cause_category, one_line_reason, source_event_id,
                    meta, created,
                ),
            )
            conn.commit()
        return row_id
    except Exception as exc:
        logger.warning("[portfolio_memory] upsert_portfolio_reaction failed: %s", exc)
        return None


def get_latest_snapshot(user_id: str) -> Optional[Dict[str, Any]]:
    """Most recent portfolio_snapshots row for a user."""
    if not user_id:
        return None
    try:
        if _use_postgres():
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM portfolio_snapshots
                    WHERE user_id = %s
                    ORDER BY snapshot_date DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
            conn.close()
            return _row_dict(row) if row else None

        conn = _get_conn()
        row = conn.execute(
            """
            SELECT * FROM portfolio_snapshots
            WHERE user_id = ?
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return _row_dict(row) if row else None
    except Exception as exc:
        logger.warning("[portfolio_memory] get_latest_snapshot failed: %s", exc)
        return None


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        d = dict(row)
    else:
        d = dict(row)
    if d.get("metadata") and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except json.JSONDecodeError:
            pass
    if d.get("sector_exposures") and isinstance(d["sector_exposures"], str):
        try:
            d["sector_exposures"] = json.loads(d["sector_exposures"])
        except json.JSONDecodeError:
            pass
    if d.get("positions_json") and isinstance(d["positions_json"], str):
        try:
            d["positions_json"] = json.loads(d["positions_json"])
        except json.JSONDecodeError:
            pass
    return d


def emit_position_added(
    user_id: str,
    ticker: str,
    *,
    shares: float,
    entry_price: float,
    sector: str = "",
    source: str = "manual",
) -> None:
    try:
        log_portfolio_event(
            user_id,
            "position_added",
            symbol=ticker,
            title=f"You added {ticker.upper()}",
            description=f"{ticker.upper()} became part of your portfolio.",
            metadata={
                "shares": shares,
                "entry_price": entry_price,
                "sector": sector,
                "source": source,
            },
        )
    except Exception as exc:
        logger.debug("[portfolio_memory] emit_position_added skipped: %s", exc)


def emit_position_removed(
    user_id: str,
    ticker: str,
    *,
    reason: str = "closed",
    realised_pnl: Optional[float] = None,
) -> None:
    try:
        log_portfolio_event(
            user_id,
            "position_removed",
            symbol=ticker,
            title=f"You removed {ticker.upper()}",
            description=f"{ticker.upper()} is no longer in your open portfolio.",
            metadata={"reason": reason, "realised_pnl": realised_pnl},
        )
    except Exception as exc:
        logger.debug("[portfolio_memory] emit_position_removed skipped: %s", exc)


def emit_import_events(
    user_id: str,
    *,
    applied_tickers: List[str],
    removed_tickers: List[str],
    items_by_ticker: Dict[str, Dict[str, Any]],
    prior_aggregated: Dict[str, Dict[str, Any]],
    source: str,
    full_snapshot: bool,
) -> None:
    """Log portfolio import and per-ticker add/remove/size-change events."""
    try:
        if not applied_tickers and not removed_tickers:
            return
        log_portfolio_event(
            user_id,
            "portfolio_imported",
            title="Portfolio updated",
            description="Your holdings were imported or refreshed.",
            metadata={
                "applied": applied_tickers,
                "removed": removed_tickers,
                "source": source,
                "full_snapshot": full_snapshot,
            },
        )
        for t in removed_tickers:
            emit_position_removed(user_id, t, reason="import_full_snapshot_replace")

        for t in applied_tickers:
            item = items_by_ticker.get(t, {})
            new_shares = float(item.get("shares") or 0)
            prior = prior_aggregated.get(t)
            if prior is None:
                emit_position_added(
                    user_id,
                    t,
                    shares=new_shares,
                    entry_price=float(item.get("avg_cost") or item.get("entry_price") or 0),
                    sector=str(item.get("sector") or ""),
                    source=source,
                )
                continue
            old_shares = float(prior.get("shares") or 0)
            if old_shares <= 0:
                emit_position_added(
                    user_id,
                    t,
                    shares=new_shares,
                    entry_price=float(item.get("avg_cost") or prior.get("avg_cost") or 0),
                    sector=str(prior.get("sector") or ""),
                    source=source,
                )
            elif new_shares > old_shares * (1 + _SIZE_CHANGE_THRESHOLD):
                log_portfolio_event(
                    user_id,
                    "position_size_increased",
                    symbol=t,
                    title=f"You increased {t}",
                    description=f"Your {t} position size grew on import.",
                    metadata={"old_shares": old_shares, "new_shares": new_shares},
                )
            elif new_shares < old_shares * (1 - _SIZE_CHANGE_THRESHOLD):
                log_portfolio_event(
                    user_id,
                    "position_size_decreased",
                    symbol=t,
                    title=f"You reduced {t}",
                    description=f"Your {t} position size shrank on import.",
                    metadata={"old_shares": old_shares, "new_shares": new_shares},
                )
    except Exception as exc:
        logger.debug("[portfolio_memory] emit_import_events skipped: %s", exc)


def snapshot_table_columns() -> List[str]:
    """Return column names on portfolio_snapshots (for tests)."""
    try:
        if _use_postgres():
            return []
        conn = _get_conn()
        return [
            r["name"]
            for r in conn.execute("PRAGMA table_info(portfolio_snapshots)").fetchall()
        ]
    except Exception:
        return []
