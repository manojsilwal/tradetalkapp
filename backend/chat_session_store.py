"""
Durable chat session rows in SQLite (same progress.db as preferences/CORAL).

Survives uvicorn/Render restarts when the client keeps the same session_id.
Multi-worker / multi-host deployments need a shared store (Postgres/Redis) instead.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from .user_preferences import DB_PATH

logger = logging.getLogger(__name__)

_local = threading.local()

# Subset of ChatSession fields stored in JSON (not system_prompt — rebuilt each message).
_PAYLOAD_KEYS = (
    "sticky_state",
    "rag_prewarm",
    "last_user_message",
    "last_assistant_text",
    "last_evidence_contract",
    "last_chat_meta",
)


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "chat_sess_conn"):
        _local.chat_sess_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.chat_sess_conn.row_factory = sqlite3.Row
    return _local.chat_sess_conn


def init_chat_sessions_db() -> None:
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            assembled_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_expires ON chat_sessions(expires_at);
        """
    )
    conn.commit()
    logger.info("[ChatSessionStore] chat_sessions table ready")


def _serialize_payload(sess: Any) -> str:
    """Extract JSON-serializable fields from ChatSession."""
    data = {
        "sticky_state": dict(getattr(sess, "sticky_state", None) or {}),
        "rag_prewarm": dict(getattr(sess, "rag_prewarm", None) or {}),
        "last_user_message": getattr(sess, "last_user_message", "") or "",
        "last_assistant_text": getattr(sess, "last_assistant_text", "") or "",
        "last_evidence_contract": getattr(sess, "last_evidence_contract", None),
        "last_chat_meta": dict(getattr(sess, "last_chat_meta", None) or {}),
    }
    return json.dumps(data, default=str)


def apply_stored_payload(sess: Any, payload: Dict[str, Any]) -> None:
    for k in _PAYLOAD_KEYS:
        if k not in payload:
            continue
        v = payload.get(k)
        if k == "sticky_state":
            sess.sticky_state = dict(v or {})
        elif k == "rag_prewarm":
            sess.rag_prewarm = dict(v or {})
        elif k == "last_user_message":
            sess.last_user_message = str(v or "")
        elif k == "last_assistant_text":
            sess.last_assistant_text = str(v or "")
        elif k == "last_evidence_contract":
            sess.last_evidence_contract = v if v is not None else None
        elif k == "last_chat_meta":
            sess.last_chat_meta = dict(v or {})


def save_session_row(
    session_id: str,
    user_id: Optional[str],
    assembled_at: float,
    expires_at: float,
    sess: Any,
) -> None:
    """Upsert session row (sync)."""
    try:
        payload = _serialize_payload(sess)
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO chat_sessions (session_id, user_id, assembled_at, expires_at, payload)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id = excluded.user_id,
                assembled_at = excluded.assembled_at,
                expires_at = excluded.expires_at,
                payload = excluded.payload
            """,
            (session_id, user_id, assembled_at, expires_at, payload),
        )
        conn.commit()
    except Exception as e:
        logger.warning("[ChatSessionStore] save failed: %s", e)


def load_session_row(session_id: str) -> Optional[Dict[str, Any]]:
    """Return row dict or None. Keys: session_id, user_id, assembled_at, expires_at, payload (parsed)."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT session_id, user_id, assembled_at, expires_at, payload FROM chat_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"] or "{}")
        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "assembled_at": float(row["assembled_at"]),
            "expires_at": float(row["expires_at"]),
            "payload": payload,
        }
    except Exception as e:
        logger.warning("[ChatSessionStore] load failed: %s", e)
        return None


def delete_session_row(session_id: str) -> None:
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
    except Exception as e:
        logger.warning("[ChatSessionStore] delete failed: %s", e)


def prune_expired_rows(now: Optional[float] = None) -> int:
    """Delete expired session rows. Returns count deleted."""
    t = now if now is not None else time.time()
    try:
        conn = _get_conn()
        cur = conn.execute("DELETE FROM chat_sessions WHERE expires_at < ?", (t,))
        conn.commit()
        return cur.rowcount or 0
    except Exception as e:
        logger.warning("[ChatSessionStore] prune failed: %s", e)
        return 0


def user_matches_row(stored_uid: Optional[str], request_uid: Optional[str]) -> bool:
    """Same user_id or both anonymous."""
    return (stored_uid or None) == (request_uid or None)
