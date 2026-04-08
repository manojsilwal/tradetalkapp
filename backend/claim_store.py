"""
Phase C — minimal entity / claim store (SQLite on progress.db).

Entities are keyed by symbol (e.g. equity ticker). Claims hold a short statement plus
an optional source reference (URL, tool id, filing id) for audit — not a full knowledge graph.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional

from . import user_preferences as _prefs
from .migrations.runner import run_migrations

logger = logging.getLogger(__name__)
_local = threading.local()


def _db_path() -> str:
    return _prefs.DB_PATH


def _conn():
    if not hasattr(_local, "claim_conn"):
        import sqlite3

        _local.claim_conn = sqlite3.connect(_db_path(), check_same_thread=False)
        _local.claim_conn.row_factory = sqlite3.Row
    return _local.claim_conn


def reset_thread_local_connection() -> None:
    if hasattr(_local, "claim_conn"):
        try:
            _local.claim_conn.close()
        except Exception:
            pass
        delattr(_local, "claim_conn")


def init_claim_store_db() -> None:
    run_migrations(_db_path(), "claim_store")
    logger.info("[ClaimStore] SQLite tables ready")


def _now() -> float:
    return time.time()


def ensure_entity_ticker(symbol: str, *, display_name: str = "") -> int:
    """Return entity id for an uppercase ticker; create if missing."""
    sym = (symbol or "").strip().upper()[:32]
    if not sym or len(sym) < 1:
        raise ValueError("invalid symbol")
    conn = _conn()
    row = conn.execute("SELECT id FROM claim_entities WHERE symbol = ?", (sym,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """INSERT INTO claim_entities (kind, symbol, display_name, created_at)
           VALUES ('ticker', ?, ?, ?)""",
        (sym, (display_name or sym)[:256], _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_claim(
    entity_id: int,
    claim_text: str,
    *,
    source_ref: str = "",
    confidence: Optional[float] = None,
    status: str = "active",
) -> int:
    txt = (claim_text or "").strip()[:8000]
    if not txt:
        return -1
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO claim_rows (entity_id, claim_text, source_ref, confidence, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            int(entity_id),
            txt,
            (source_ref or "")[:2048],
            confidence,
            (status or "active")[:32],
            _now(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_claim_for_symbol(
    symbol: str,
    claim_text: str,
    *,
    source_ref: str = "",
    confidence: Optional[float] = None,
) -> int:
    eid = ensure_entity_ticker(symbol)
    return add_claim(eid, claim_text, source_ref=source_ref, confidence=confidence)


def list_claims_for_symbol(symbol: str, n: int = 20) -> List[dict[str, Any]]:
    sym = (symbol or "").strip().upper()[:32]
    n = max(1, min(100, int(n)))
    conn = _conn()
    rows = conn.execute(
        """SELECT c.id, c.claim_text, c.source_ref, c.confidence, c.status, c.created_at,
                  e.symbol, e.display_name
           FROM claim_rows c
           JOIN claim_entities e ON e.id = c.entity_id
           WHERE e.symbol = ? AND c.status = 'active'
           ORDER BY c.created_at DESC LIMIT ?""",
        (sym, n),
    ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict[str, Any]:
    conn = _conn()
    ne = conn.execute("SELECT COUNT(*) FROM claim_entities").fetchone()[0]
    nc = conn.execute("SELECT COUNT(*) FROM claim_rows WHERE status = 'active'").fetchone()[0]
    return {"entities": int(ne), "active_claims": int(nc)}
