"""Chat sessions and message history on Cloud SQL Postgres."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .auth_pg import _get_conn

logger = logging.getLogger(__name__)

_PAYLOAD_KEYS = (
    "sticky_state",
    "rag_prewarm",
    "last_user_message",
    "last_assistant_text",
    "last_evidence_contract",
    "last_chat_meta",
)


def _serialize_payload(sess: Any) -> str:
    data = {
        "sticky_state": dict(getattr(sess, "sticky_state", None) or {}),
        "rag_prewarm": dict(getattr(sess, "rag_prewarm", None) or {}),
        "last_user_message": getattr(sess, "last_user_message", "") or "",
        "last_assistant_text": getattr(sess, "last_assistant_text", "") or "",
        "last_evidence_contract": getattr(sess, "last_evidence_contract", None),
        "last_chat_meta": dict(getattr(sess, "last_chat_meta", None) or {}),
    }
    return json.dumps(data, default=str)


def save_session_row(
    session_id: str,
    user_id: Optional[str],
    assembled_at: float,
    expires_at: float,
    sess: Any,
) -> None:
    try:
        payload = _serialize_payload(sess)
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (session_id, user_id, assembled_at, expires_at, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    assembled_at = EXCLUDED.assembled_at,
                    expires_at = EXCLUDED.expires_at,
                    payload = EXCLUDED.payload
                """,
                (session_id, user_id, assembled_at, expires_at, payload),
            )
        conn.commit()
    except Exception as e:
        logger.warning("[chat_store_pg] save failed: %s", e)


def load_session_row(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, user_id, assembled_at, expires_at, payload
                FROM chat_sessions WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
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
        logger.warning("[chat_store_pg] load failed: %s", e)
        return None


def delete_session_row(session_id: str) -> None:
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))
        conn.commit()
    except Exception as e:
        logger.warning("[chat_store_pg] delete failed: %s", e)


def prune_expired_rows(now: Optional[float] = None) -> int:
    t = now if now is not None else time.time()
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_sessions WHERE expires_at < %s", (t,))
            deleted = cur.rowcount or 0
        conn.commit()
        return deleted
    except Exception as e:
        logger.warning("[chat_store_pg] prune failed: %s", e)
        return 0


def save_message(
    user_id: str,
    session_id: str,
    role: str,
    content: str,
    created_at: float,
) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_message_history (user_id, session_id, role, content, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, session_id, role, content, created_at),
        )
    conn.commit()


def load_messages(
    user_id: str,
    session_id: str,
    limit: int,
) -> List[Dict[str, str]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT role, content FROM chat_message_history
            WHERE user_id = %s AND session_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (user_id, session_id, limit),
        )
        rows = cur.fetchall()
    rows = list(reversed(rows))
    out: List[Dict[str, str]] = []
    for r in rows:
        role = r["role"]
        if role in ("user", "assistant"):
            out.append({"role": role, "content": str(r["content"])})
    return out


def list_sessions(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """List chat sessions for a user with summary metadata."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                h.session_id,
                MIN(h.created_at) AS started_at,
                MAX(h.created_at) AS last_activity,
                COUNT(*)::int AS message_count,
                (
                    SELECT h2.content
                    FROM chat_message_history h2
                    WHERE h2.user_id = h.user_id
                      AND h2.session_id = h.session_id
                      AND h2.role = 'user'
                    ORDER BY h2.created_at ASC, h2.id ASC
                    LIMIT 1
                ) AS title
            FROM chat_message_history h
            WHERE h.user_id = %s
            GROUP BY h.session_id, h.user_id
            ORDER BY last_activity DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        title = (r.get("title") or "").strip()
        if len(title) > 120:
            title = title[:117] + "..."
        out.append({
            "session_id": r["session_id"],
            "started_at": float(r["started_at"]),
            "last_activity": float(r["last_activity"]),
            "message_count": int(r["message_count"]),
            "title": title or "Chat session",
        })
    return out


def session_belongs_to_user(user_id: str, session_id: str) -> bool:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM chat_message_history
            WHERE user_id = %s AND session_id = %s
            LIMIT 1
            """,
            (user_id, session_id),
        )
        if cur.fetchone():
            return True
        cur.execute(
            "SELECT 1 FROM chat_sessions WHERE session_id = %s AND user_id = %s LIMIT 1",
            (session_id, user_id),
        )
        return cur.fetchone() is not None
