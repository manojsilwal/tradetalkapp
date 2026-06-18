"""User authentication persistence on Cloud SQL Postgres."""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .postgres_config import postgres_connection_kwargs, postgres_dsn, postgres_enabled
from .progress_db import resolve_progress_db_path

logger = logging.getLogger(__name__)

_local = None


def enabled() -> bool:
    return postgres_enabled()


def _connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    return psycopg2.connect(postgres_dsn(), cursor_factory=RealDictCursor)


def _get_conn():
    global _local
    import threading

    if _local is None:
        _local = threading.local()
    if not hasattr(_local, "conn") or _local.conn.closed:
        _local.conn = _connect()
    return _local.conn


def init_schema() -> None:
    mig_dir = Path(__file__).resolve().parent / "migrations" / "postgres"
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute((mig_dir / "004_users_and_chat.sql").read_text(encoding="utf-8"))
        cur.execute((mig_dir / "005_auth_otp.sql").read_text(encoding="utf-8"))
    conn.commit()
    logger.info("[auth_pg] schema ready on %s", postgres_connection_kwargs()["host"])


def init_otp_schema() -> None:
    mig = Path(__file__).resolve().parent / "migrations" / "postgres" / "005_auth_otp.sql"
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(mig.read_text(encoding="utf-8"))
    conn.commit()


def migrate_from_sqlite_if_needed() -> None:
    """One-time copy of users/preferences/chat tables from local progress.db."""
    sqlite_path = resolve_progress_db_path()
    if not Path(sqlite_path).is_file():
        return
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        row = cur.fetchone()
        if row and int(row["n"]) > 0:
            return
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    try:
        _copy_table(src, conn, "users", [
            "id", "email", "name", "avatar", "password_hash", "created_at",
        ], conflict="(id) DO NOTHING")
        _copy_table(src, conn, "user_preferences", [
            "user_id", "preferences", "signals", "updated_at",
        ], conflict="(user_id) DO NOTHING")
        _copy_table(src, conn, "chat_sessions", [
            "session_id", "user_id", "assembled_at", "expires_at", "payload",
        ], conflict="(session_id) DO NOTHING")
        _copy_chat_history(src, conn)
    finally:
        src.close()
    conn.commit()
    logger.info("[auth_pg] migrated user/chat data from SQLite")


def _copy_table(
    src: sqlite3.Connection,
    conn,
    table: str,
    cols: list[str],
    *,
    conflict: str,
) -> None:
    try:
        rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    except sqlite3.OperationalError:
        return
    if not rows:
        return
    with conn.cursor() as cur:
        for r in rows:
            d = dict(r)
            cur.execute(
                f"""
                INSERT INTO {table} ({", ".join(cols)})
                VALUES ({", ".join("%s" for _ in cols)})
                ON CONFLICT {conflict}
                """,
                [d.get(c) for c in cols],
            )
    logger.info("[auth_pg] migrated %d rows into %s", len(rows), table)


def _copy_chat_history(src: sqlite3.Connection, conn) -> None:
    try:
        rows = src.execute(
            "SELECT user_id, session_id, role, content, created_at FROM chat_message_history"
        ).fetchall()
    except sqlite3.OperationalError:
        return
    if not rows:
        return
    with conn.cursor() as cur:
        for r in rows:
            d = dict(r)
            cur.execute(
                """
                INSERT INTO chat_message_history (user_id, session_id, role, content, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (d["user_id"], d["session_id"], d["role"], d["content"], d["created_at"]),
            )
    logger.info("[auth_pg] migrated %d chat_message_history rows", len(rows))


def upsert_user(google_id: str, email: str, name: str, avatar: str) -> Dict[str, Any]:
    conn = _get_conn()
    now = time.time()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (id, email, name, avatar, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                email = EXCLUDED.email,
                name = EXCLUDED.name,
                avatar = EXCLUDED.avatar
            """,
            (google_id, email, name, avatar, now),
        )
    conn.commit()
    return {"id": google_id, "email": email, "name": name, "avatar": avatar}


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, email, name, avatar FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    if not row:
        return None
    return dict(row)


def create_manual_user(
    user_id: str,
    email: str,
    name: str,
    password_hash: str,
) -> Dict[str, Any]:
    conn = _get_conn()
    now = time.time()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (id, email, name, avatar, password_hash, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, email, name, "", password_hash, now),
        )
    conn.commit()
    return {"id": user_id, "email": email, "name": name, "avatar": ""}


def email_exists(email: str) -> bool:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE LOWER(email) = %s LIMIT 1", (email,))
        return cur.fetchone() is not None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE LOWER(email) = %s LIMIT 1", (email,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_user_auth_row(user_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def set_user_password(user_id: str, password_hash: str) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (password_hash, user_id),
        )
    conn.commit()


def insert_otp_session(
    session_id: str,
    user_id: str,
    otp_hash: str,
    expires_at: float,
) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO auth_otp_sessions (session_id, user_id, otp_hash, expires_at, attempts)
            VALUES (%s, %s, %s, %s, 0)
            """,
            (session_id, user_id, otp_hash, expires_at),
        )
    conn.commit()


def get_otp_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT session_id, user_id, otp_hash, expires_at, attempts FROM auth_otp_sessions WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def delete_otp_session(session_id: str) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM auth_otp_sessions WHERE session_id = %s", (session_id,))
    conn.commit()


def update_otp_attempts(session_id: str, attempts: int) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE auth_otp_sessions SET attempts = %s WHERE session_id = %s",
            (attempts, session_id),
        )
    conn.commit()
