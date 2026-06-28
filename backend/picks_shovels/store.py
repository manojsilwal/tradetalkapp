"""
SQLite persistence for the Picks & Shovels Momentum Finder.

Mirrors ``backend/actionable_companies.py`` snapshot persistence: a full scan is
written as one snapshot row plus N ranked ``ps_rows`` (payload JSON per ticker).
Reads serve the latest snapshot to the API/UI; a fresh snapshot (< TTL) is reused
instead of re-hitting Yahoo.

Env knobs:
  PICKS_SHOVELS_DB_PATH      explicit SQLite file (tests use a temp file)
  PICKS_SHOVELS_CACHE_TTL_S  snapshot freshness window (default 3600)
  TRADETALK_DATA_DIR         shared data dir fallback
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from .. import durable_snapshot

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_lock = threading.Lock()
_DURABLE_KIND = "picks_shovels"


def cache_ttl_s() -> int:
    return int(os.environ.get("PICKS_SHOVELS_CACHE_TTL_S", "604800") or "604800")


def _db_path() -> str:
    explicit = os.environ.get("PICKS_SHOVELS_DB_PATH", "").strip()
    if explicit:
        parent = os.path.dirname(explicit)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return explicit
    data_dir = os.environ.get("TRADETALK_DATA_DIR", "").strip()
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "picks_shovels.db")
    return os.path.join(_BACKEND_DIR, "picks_shovels.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ps_snapshots (
               snapshot_id   TEXT PRIMARY KEY,
               created_at    REAL NOT NULL,
               universe_size INTEGER NOT NULL,
               scored        INTEGER NOT NULL,
               skipped       INTEGER NOT NULL,
               meta_json     TEXT NOT NULL DEFAULT '{}'
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ps_rows (
               snapshot_id   TEXT NOT NULL,
               ticker        TEXT NOT NULL,
               final_score   REAL,
               theme_primary TEXT,
               hiddenness    TEXT,
               payload_json  TEXT NOT NULL,
               PRIMARY KEY (snapshot_id, ticker)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ps_rows_score "
        "ON ps_rows (snapshot_id, final_score DESC)"
    )
    return conn


def persist_snapshot(
    snapshot_id: str,
    rows: List[Dict[str, Any]],
    *,
    universe_size: int,
    skipped: int,
    created_at: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    ts = created_at if created_at is not None else time.time()
    with _db_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM ps_rows WHERE snapshot_id = ?", (snapshot_id,))
            conn.execute(
                "INSERT OR REPLACE INTO ps_snapshots "
                "(snapshot_id, created_at, universe_size, scored, skipped, meta_json) "
                "VALUES (?,?,?,?,?,?)",
                (snapshot_id, ts, universe_size, len(rows), skipped, json.dumps(meta or {})),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO ps_rows "
                "(snapshot_id, ticker, final_score, theme_primary, hiddenness, payload_json) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (
                        snapshot_id,
                        r["ticker"],
                        float(r["final_score"]) if r.get("final_score") is not None else None,
                        (r.get("themes") or [None])[0],
                        r.get("hiddenness_level"),
                        json.dumps(r, default=str),
                    )
                    for r in rows
                ],
            )
            conn.commit()
        finally:
            conn.close()
    # Durable mirror (Postgres in prod) so the snapshot survives Cloud Run cold
    # starts and is readable by any instance. No-op when durable store inactive.
    durable_snapshot.put(
        _DURABLE_KIND, snapshot_id, ts,
        {"rows": rows, "universe_size": universe_size, "scored": len(rows), "skipped": skipped},
        meta or {},
    )
    return len(rows)


def latest_snapshot_meta() -> Optional[Dict[str, Any]]:
    d = durable_snapshot.get_latest(_DURABLE_KIND)
    if d:
        p = d.get("payload") or {}
        return {
            "snapshot_id": d["snapshot_id"],
            "created_at": d["created_at"],
            "universe_size": int(p.get("universe_size") or 0),
            "scored": int(p.get("scored") or len(p.get("rows") or [])),
            "skipped": int(p.get("skipped") or 0),
            "meta": d.get("meta") or {},
        }
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM ps_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return {
        "snapshot_id": row["snapshot_id"],
        "created_at": float(row["created_at"]),
        "universe_size": int(row["universe_size"]),
        "scored": int(row["scored"]),
        "skipped": int(row["skipped"]),
        "meta": json.loads(row["meta_json"] or "{}"),
    }


def _durable_rows(snapshot_id: str) -> Optional[List[Dict[str, Any]]]:
    d = durable_snapshot.get_latest(_DURABLE_KIND)
    if d and d.get("snapshot_id") == snapshot_id:
        rows = (d.get("payload") or {}).get("rows") or []
        return sorted(rows, key=lambda r: r.get("final_score") or 0, reverse=True)
    return None


def load_snapshot_rows(snapshot_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
    """All rows for a snapshot, ranked by final score (desc). Filtering happens in the router."""
    durable = _durable_rows(snapshot_id)
    if durable is not None:
        return durable[: int(limit)]
    with _db_lock:
        conn = _connect()
        try:
            raw = conn.execute(
                "SELECT payload_json FROM ps_rows WHERE snapshot_id = ? "
                "ORDER BY final_score DESC LIMIT ?",
                (snapshot_id, int(limit)),
            ).fetchall()
        finally:
            conn.close()
    return [json.loads(r["payload_json"]) for r in raw]


def load_row(snapshot_id: str, ticker: str) -> Optional[Dict[str, Any]]:
    durable = _durable_rows(snapshot_id)
    if durable is not None:
        for r in durable:
            if (r.get("ticker") or "").upper() == ticker.upper():
                return r
        return None
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM ps_rows WHERE snapshot_id = ? AND ticker = ?",
                (snapshot_id, ticker.upper()),
            ).fetchone()
        finally:
            conn.close()
    return json.loads(row["payload_json"]) if row else None


def fresh_snapshot_meta(ttl_s: Optional[int] = None) -> Optional[Dict[str, Any]]:
    meta = latest_snapshot_meta()
    if not meta:
        return None
    ttl = ttl_s if ttl_s is not None else cache_ttl_s()
    if time.time() - meta["created_at"] > ttl:
        return None
    return meta
