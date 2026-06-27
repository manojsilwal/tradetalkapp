"""
Durable, instance-independent snapshot store for precomputed "global" pages.

Problem (see docs/PRECOMPUTED_PAGES_PLAN.md §1): Picks & Shovels and Narrative
Radar persist snapshots in local SQLite, which is **ephemeral per Cloud Run
instance** — a snapshot written by a user/cron scan disappears on the next cold
start, leaving a blank page. This module provides a tiny **latest-per-kind** blob
store that is durable across instances by reusing the repo's Postgres dual-write
pattern (matching ``backend/fund_leaderboard_store.py``).

Design:
  - One row per ``kind`` (e.g. "picks_shovels", "narrative_radar") = always the
    latest snapshot. ``put`` replaces it; ``get_latest`` reads it.
  - Backend = Postgres when ``postgres_enabled()`` (durable in prod), else a local
    SQLite file (``DURABLE_SNAPSHOT_DB_PATH`` or ``TRADETALK_DATA_DIR``).
  - ``active()`` is False when neither Postgres nor an explicit SQLite path is set,
    so local dev / unit tests behave exactly as before (the page's own SQLite store
    remains the source of truth) with zero new files or pollution.

Payload is a JSON blob (``payload_json``) — the page's serving rows + meta — so the
existing scoring/engine code is untouched and server-side filtering keeps working
by reconstructing from the rows list.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_locks: Dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def _use_postgres() -> bool:
    try:
        from .postgres_config import postgres_enabled

        return postgres_enabled()
    except Exception:
        return False


def _explicit_sqlite_path() -> str:
    explicit = os.environ.get("DURABLE_SNAPSHOT_DB_PATH", "").strip()
    if explicit:
        return explicit
    data_dir = os.environ.get("TRADETALK_DATA_DIR", "").strip()
    if data_dir:
        return os.path.join(data_dir, "page_snapshots.db")
    return ""


def active() -> bool:
    """Durable mirror is active only when a durable backend is configured.

    When False (no Postgres, no explicit/shared data dir), callers fall back to
    their own local SQLite store — preserving current local/test behavior exactly.
    """
    if _use_postgres():
        return True
    return bool(_explicit_sqlite_path())


def _sqlite_path() -> str:
    return _explicit_sqlite_path() or os.path.join(_BACKEND_DIR, "page_snapshots.db")


def _pg_connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from .postgres_config import postgres_dsn

    return psycopg2.connect(postgres_dsn(), cursor_factory=RealDictCursor)


def _ph(sql: str) -> str:
    return sql.replace("?", "%s") if _use_postgres() else sql


def _lock_for(path: str) -> threading.Lock:
    with _lock_guard:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


@contextmanager
def _cursor(commit: bool = False):
    if _use_postgres():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()
    else:
        path = _sqlite_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with _lock_for(path):
            conn = sqlite3.connect(path, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                yield cur
                if commit:
                    conn.commit()
            finally:
                conn.close()


def _ensure_schema(cur) -> None:
    # Portable DDL (valid in both SQLite and Postgres).
    cur.execute(
        """CREATE TABLE IF NOT EXISTS page_snapshots (
               kind          TEXT PRIMARY KEY,
               snapshot_id   TEXT NOT NULL,
               created_at    DOUBLE PRECISION NOT NULL,
               payload_json  TEXT NOT NULL,
               meta_json     TEXT NOT NULL DEFAULT '{}',
               updated_at    DOUBLE PRECISION NOT NULL
           )"""
    )


def put(
    kind: str,
    snapshot_id: str,
    created_at: float,
    payload: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """Store/replace the latest snapshot for ``kind``. Never raises."""
    if not active():
        return False
    try:
        with _cursor(commit=True) as cur:
            _ensure_schema(cur)
            cur.execute(_ph("DELETE FROM page_snapshots WHERE kind = ?"), (kind,))
            cur.execute(
                _ph(
                    "INSERT INTO page_snapshots (kind, snapshot_id, created_at, payload_json, meta_json, updated_at) "
                    "VALUES (?,?,?,?,?,?)"
                ),
                (
                    kind, snapshot_id, float(created_at),
                    json.dumps(payload, default=str), json.dumps(meta or {}, default=str),
                    time.time(),
                ),
            )
        return True
    except Exception as e:
        logger.warning("[DurableSnapshot] put(%s) failed (non-fatal): %s", kind, e)
        return False


def get_latest(kind: str) -> Optional[Dict[str, Any]]:
    """Return the latest snapshot for ``kind`` (or None). Never raises."""
    if not active():
        return None
    try:
        with _cursor() as cur:
            _ensure_schema(cur)
            cur.execute(
                _ph("SELECT snapshot_id, created_at, payload_json, meta_json FROM page_snapshots WHERE kind = ?"),
                (kind,),
            )
            row = cur.fetchone()
        if not row:
            return None
        row = dict(row)
        return {
            "snapshot_id": row["snapshot_id"],
            "created_at": float(row["created_at"]),
            "payload": json.loads(row["payload_json"] or "{}"),
            "meta": json.loads(row["meta_json"] or "{}"),
        }
    except Exception as e:
        logger.warning("[DurableSnapshot] get_latest(%s) failed (non-fatal): %s", kind, e)
        return None
