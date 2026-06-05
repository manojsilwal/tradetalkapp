"""
Portfolio timeline — joins portfolio_events + reaction memory (Your Morning v0 Phase 6).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Dict, List

from .progress_db import resolve_progress_db_path

DB_PATH = resolve_progress_db_path()
_local = threading.local()


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


def _human_line(event_type: str, symbol: str, title: str, description: str) -> str:
    if title:
        return title
    sym = f" {symbol}" if symbol else ""
    templates = {
        "position_added": f"You added{sym}",
        "position_removed": f"You removed{sym}",
        "portfolio_imported": "You updated your portfolio",
        "big_move_for_held_position": f"Big move for{sym}",
        "position_crossed_gain_threshold": f"Gain milestone for{sym}",
        "position_crossed_loss_threshold": f"Loss milestone for{sym}",
        "position_became_top_holding": f"{symbol} became your largest holding" if symbol else "Top holding changed",
        "sector_exposure_changed": "Sector exposure shifted",
    }
    return templates.get(event_type, description or event_type.replace("_", " "))


def build_timeline(user_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    """Merge portfolio events and reaction memory into a sorted feed."""
    if not user_id:
        return []
    limit = max(1, min(int(limit), 50))
    items: List[Dict[str, Any]] = []

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
                events = cur.fetchall()
                cur.execute(
                    """
                    SELECT * FROM portfolio_reaction_memory
                    WHERE user_id = %s
                    ORDER BY event_date DESC, created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                reactions = cur.fetchall()
            conn.close()
        else:
            conn = _get_conn()
            events = conn.execute(
                """
                SELECT * FROM portfolio_events
                WHERE user_id = ?
                ORDER BY event_date DESC, created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            reactions = conn.execute(
                """
                SELECT * FROM portfolio_reaction_memory
                WHERE user_id = ?
                ORDER BY event_date DESC, created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        for row in events:
            d = dict(row)
            meta = d.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            items.append({
                "id": d.get("id"),
                "kind": "portfolio_event",
                "event_type": d.get("event_type"),
                "symbol": d.get("symbol"),
                "event_date": d.get("event_date"),
                "title": _human_line(
                    d.get("event_type", ""),
                    d.get("symbol") or "",
                    d.get("title") or "",
                    d.get("description") or "",
                ),
                "description": d.get("description"),
                "metadata": meta,
                "sort_ts": float(d.get("created_at") or 0),
            })

        for row in reactions:
            d = dict(row)
            move = d.get("move_pct")
            sym = d.get("symbol") or ""
            line = d.get("one_line_reason") or f"{sym} moved {move:+.1f}%".format(move=move or 0)
            items.append({
                "id": d.get("id"),
                "kind": "reaction_memory",
                "event_type": "big_move_for_held_position",
                "symbol": sym,
                "event_date": d.get("event_date"),
                "title": line,
                "description": line,
                "metadata": {
                    "move_pct": move,
                    "portfolio_impact_pct": d.get("portfolio_impact_pct"),
                },
                "sort_ts": float(d.get("created_at") or 0),
            })

        items.sort(key=lambda x: (x.get("event_date") or "", x.get("sort_ts") or 0), reverse=True)
        return items[:limit]
    except Exception:
        return []
