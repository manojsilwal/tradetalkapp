"""
Per-page feedback persistence (star ratings + optional comments).

Anonymous submissions allowed (user_id nullable). Postgres in production,
SQLite fallback for local dev.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .progress_db import resolve_progress_db_path

logger = logging.getLogger(__name__)

DB_PATH = resolve_progress_db_path()
_local = threading.local()
MAX_COMMENT_LEN = 2000

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS page_feedback (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    page        TEXT NOT NULL,
    rating      INTEGER,
    comment     TEXT,
    symbol      TEXT,
    metadata    TEXT DEFAULT '{}',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_page_feedback_page_created
    ON page_feedback (page, created_at DESC);
"""


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


def _new_id() -> str:
    return f"pf_{uuid.uuid4().hex[:16]}"


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj or {}, default=str)


def init_page_feedback_db() -> None:
    """Create page_feedback table (idempotent)."""
    if _use_postgres():
        try:
            from pathlib import Path

            mig = (
                Path(__file__).resolve().parent
                / "migrations"
                / "postgres"
                / "008_page_feedback.sql"
            )
            conn = _pg_connect()
            with conn.cursor() as cur:
                cur.execute(mig.read_text(encoding="utf-8"))
            conn.commit()
            conn.close()
            logger.info("[page_feedback] Postgres schema ready")
        except Exception as exc:
            logger.error("[page_feedback] Postgres init failed: %s", exc)
        return

    try:
        conn = _get_conn()
        conn.executescript(_SQLITE_DDL)
        conn.commit()
        logger.info("[page_feedback] SQLite schema ready")
    except Exception as exc:
        logger.warning("[page_feedback] SQLite init skipped: %s", exc)


def save_feedback(
    *,
    user_id: Optional[str],
    page: str,
    rating: Optional[int] = None,
    comment: Optional[str] = None,
    symbol: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Persist one feedback row. Raises ValueError on invalid input."""
    page_norm = (page or "").strip()
    if not page_norm or len(page_norm) > 256:
        raise ValueError("page is required")

    comment_norm = (comment or "").strip() or None
    if comment_norm and len(comment_norm) > MAX_COMMENT_LEN:
        comment_norm = comment_norm[:MAX_COMMENT_LEN]

    rating_val: Optional[int] = None
    if rating is not None:
        rating_val = int(rating)
        if rating_val < 1 or rating_val > 5:
            raise ValueError("rating must be between 1 and 5")

    if rating_val is None and not comment_norm:
        raise ValueError("rating or comment is required")

    sym = (symbol or "").strip().upper() or None
    meta = _json_dumps(metadata)
    fid = _new_id()
    created = time.time()
    uid = (user_id or "").strip() or None

    if _use_postgres():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO page_feedback
                    (id, user_id, page, rating, comment, symbol, metadata, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (fid, uid, page_norm, rating_val, comment_norm, sym, meta, created),
                )
            conn.commit()
        finally:
            conn.close()
    else:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO page_feedback
            (id, user_id, page, rating, comment, symbol, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (fid, uid, page_norm, rating_val, comment_norm, sym, meta, created),
        )
        conn.commit()

    return fid


def feedback_summary(*, limit_pages: int = 50) -> List[Dict[str, Any]]:
    """Aggregate feedback counts and average rating per page."""
    limit_pages = max(1, min(int(limit_pages), 200))
    if _use_postgres():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        page,
                        COUNT(*)::int AS submission_count,
                        AVG(rating)::float AS avg_rating,
                        SUM(
                            CASE WHEN comment IS NOT NULL AND TRIM(comment) <> ''
                            THEN 1 ELSE 0 END
                        )::int AS comment_count
                    FROM page_feedback
                    GROUP BY page
                    ORDER BY submission_count DESC
                    LIMIT %s
                    """,
                    (limit_pages,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    conn = _get_conn()
    cur = conn.execute(
        """
        SELECT
            page,
            COUNT(*) AS submission_count,
            AVG(rating) AS avg_rating,
            SUM(
                CASE WHEN comment IS NOT NULL AND TRIM(comment) <> ''
                THEN 1 ELSE 0 END
            ) AS comment_count
        FROM page_feedback
        GROUP BY page
        ORDER BY submission_count DESC
        LIMIT ?
        """,
        (limit_pages,),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        out.append(
            {
                "page": row["page"],
                "submission_count": row["submission_count"],
                "avg_rating": float(row["avg_rating"]) if row["avg_rating"] is not None else None,
                "comment_count": row["comment_count"] or 0,
            }
        )
    return out
