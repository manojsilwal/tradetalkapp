"""
SQLite persistence for the Narrative Rotation Radar.

Mirrors ``backend/picks_shovels/store.py``: one ``nr_snapshots`` row per scan plus
N ranked ``nr_theme_rows`` (payload JSON per theme). Reads serve the latest
snapshot; a fresh snapshot (< TTL) is reused instead of re-hitting Yahoo.

Env knobs:
  NARRATIVE_RADAR_DB_PATH      explicit SQLite file (tests use a temp file)
  NARRATIVE_RADAR_CACHE_TTL_S  snapshot freshness window (default 3600)
  TRADETALK_DATA_DIR           shared data dir fallback
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
_DURABLE_KIND = "narrative_radar"
_DURABLE_ALERTS_KIND = "narrative_radar_alerts"


def cache_ttl_s() -> int:
    return int(os.environ.get("NARRATIVE_RADAR_CACHE_TTL_S", "3600") or "3600")


def _db_path() -> str:
    explicit = os.environ.get("NARRATIVE_RADAR_DB_PATH", "").strip()
    if explicit:
        parent = os.path.dirname(explicit)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return explicit
    data_dir = os.environ.get("TRADETALK_DATA_DIR", "").strip()
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "narrative_radar.db")
    return os.path.join(_BACKEND_DIR, "narrative_radar.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS nr_snapshots (
               snapshot_id   TEXT PRIMARY KEY,
               created_at    REAL NOT NULL,
               theme_count   INTEGER NOT NULL,
               scored        INTEGER NOT NULL,
               skipped       INTEGER NOT NULL,
               meta_json     TEXT NOT NULL DEFAULT '{}'
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS nr_theme_rows (
               snapshot_id      TEXT NOT NULL,
               theme_id         TEXT NOT NULL,
               lifecycle_phase  TEXT,
               exit_risk_score  REAL,
               confidence_score REAL,
               payload_json     TEXT NOT NULL,
               PRIMARY KEY (snapshot_id, theme_id)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nr_rows_phase "
        "ON nr_theme_rows (snapshot_id, lifecycle_phase)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS nr_alerts (
               alert_id      TEXT PRIMARY KEY,
               snapshot_id   TEXT NOT NULL,
               created_at    REAL NOT NULL,
               theme_id      TEXT,
               alert_type    TEXT NOT NULL,
               severity      TEXT NOT NULL,
               title         TEXT NOT NULL,
               explanation   TEXT,
               payload_json  TEXT NOT NULL DEFAULT '{}'
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nr_alerts_snapshot ON nr_alerts (snapshot_id, created_at DESC)")
    return conn


def persist_snapshot(
    snapshot_id: str,
    rows: List[Dict[str, Any]],
    *,
    theme_count: int,
    skipped: int,
    created_at: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    ts = created_at if created_at is not None else time.time()
    with _db_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM nr_theme_rows WHERE snapshot_id = ?", (snapshot_id,))
            conn.execute(
                "INSERT OR REPLACE INTO nr_snapshots "
                "(snapshot_id, created_at, theme_count, scored, skipped, meta_json) "
                "VALUES (?,?,?,?,?,?)",
                (snapshot_id, ts, theme_count, len(rows), skipped, json.dumps(meta or {})),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO nr_theme_rows "
                "(snapshot_id, theme_id, lifecycle_phase, exit_risk_score, confidence_score, payload_json) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (
                        snapshot_id,
                        r["theme_id"],
                        r.get("lifecycle_phase"),
                        (r.get("scores") or {}).get("theme_exit_risk_score"),
                        r.get("confidence_score"),
                        json.dumps(r, default=str),
                    )
                    for r in rows
                ],
            )
            conn.commit()
        finally:
            conn.close()
    # Durable mirror (Postgres in prod) so the snapshot survives Cloud Run cold starts.
    durable_snapshot.put(
        _DURABLE_KIND, snapshot_id, ts,
        {"rows": rows, "theme_count": theme_count, "scored": len(rows), "skipped": skipped},
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
            "theme_count": int(p.get("theme_count") or 0),
            "scored": int(p.get("scored") or len(p.get("rows") or [])),
            "skipped": int(p.get("skipped") or 0),
            "meta": d.get("meta") or {},
        }
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM nr_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return {
        "snapshot_id": row["snapshot_id"],
        "created_at": float(row["created_at"]),
        "theme_count": int(row["theme_count"]),
        "scored": int(row["scored"]),
        "skipped": int(row["skipped"]),
        "meta": json.loads(row["meta_json"] or "{}"),
    }


def _durable_rows(snapshot_id: str) -> Optional[List[Dict[str, Any]]]:
    d = durable_snapshot.get_latest(_DURABLE_KIND)
    if d and d.get("snapshot_id") == snapshot_id:
        return (d.get("payload") or {}).get("rows") or []
    return None


def load_snapshot_rows(snapshot_id: str) -> List[Dict[str, Any]]:
    durable = _durable_rows(snapshot_id)
    if durable is not None:
        return durable
    with _db_lock:
        conn = _connect()
        try:
            raw = conn.execute(
                "SELECT payload_json FROM nr_theme_rows WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
        finally:
            conn.close()
    return [json.loads(r["payload_json"]) for r in raw]


def load_row(snapshot_id: str, theme_id: str) -> Optional[Dict[str, Any]]:
    durable = _durable_rows(snapshot_id)
    if durable is not None:
        for r in durable:
            if r.get("theme_id") == theme_id:
                return r
        return None
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM nr_theme_rows WHERE snapshot_id = ? AND theme_id = ?",
                (snapshot_id, theme_id),
            ).fetchone()
        finally:
            conn.close()
    return json.loads(row["payload_json"]) if row else None


def persist_alerts(snapshot_id: str, alerts: List[Dict[str, Any]], *, created_at: Optional[float] = None) -> int:
    import uuid
    ts = created_at if created_at is not None else time.time()
    with _db_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM nr_alerts WHERE snapshot_id = ?", (snapshot_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO nr_alerts "
                "(alert_id, snapshot_id, created_at, theme_id, alert_type, severity, title, explanation, payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        uuid.uuid4().hex, snapshot_id, ts, a.get("theme_id"),
                        a.get("alert_type"), a.get("severity"), a.get("title"),
                        a.get("explanation"), json.dumps(a, default=str),
                    )
                    for a in alerts
                ],
            )
            conn.commit()
        finally:
            conn.close()
    durable_snapshot.put(_DURABLE_ALERTS_KIND, snapshot_id, ts, {"alerts": alerts}, {})
    return len(alerts)


def load_alerts(snapshot_id: str, *, severity: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    d = durable_snapshot.get_latest(_DURABLE_ALERTS_KIND)
    if d and d.get("snapshot_id") == snapshot_id:
        alerts = (d.get("payload") or {}).get("alerts") or []
        if severity:
            alerts = [a for a in alerts if a.get("severity") == severity]
        return alerts[: int(limit)]
    with _db_lock:
        conn = _connect()
        try:
            if severity:
                raw = conn.execute(
                    "SELECT payload_json FROM nr_alerts WHERE snapshot_id = ? AND severity = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (snapshot_id, severity, int(limit)),
                ).fetchall()
            else:
                raw = conn.execute(
                    "SELECT payload_json FROM nr_alerts WHERE snapshot_id = ? ORDER BY created_at DESC LIMIT ?",
                    (snapshot_id, int(limit)),
                ).fetchall()
        finally:
            conn.close()
    return [json.loads(r["payload_json"]) for r in raw]


def fresh_snapshot_meta(ttl_s: Optional[int] = None) -> Optional[Dict[str, Any]]:
    meta = latest_snapshot_meta()
    if not meta:
        return None
    ttl = ttl_s if ttl_s is not None else cache_ttl_s()
    if time.time() - meta["created_at"] > ttl:
        return None
    return meta
