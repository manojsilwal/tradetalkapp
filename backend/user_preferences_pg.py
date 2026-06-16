"""User preferences persistence on Cloud SQL Postgres."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from .auth_pg import _get_conn

logger = logging.getLogger(__name__)


def ensure_row(user_id: str, defaults: Dict[str, Any]) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_preferences (user_id, preferences, signals, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, json.dumps(defaults), "{}", time.time()),
        )
    conn.commit()


def get_preferences_row(user_id: str) -> Dict[str, Any]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT preferences, signals FROM user_preferences WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"preferences": "{}", "signals": "{}"}
    return dict(row)


def update_preferences_row(
    user_id: str,
    preferences_json: str,
    *,
    signals_json: str | None = None,
) -> None:
    conn = _get_conn()
    now = time.time()
    with conn.cursor() as cur:
        if signals_json is not None:
            cur.execute(
                """
                UPDATE user_preferences
                SET preferences = %s, signals = %s, updated_at = %s
                WHERE user_id = %s
                """,
                (preferences_json, signals_json, now, user_id),
            )
        else:
            cur.execute(
                """
                UPDATE user_preferences
                SET preferences = %s, updated_at = %s
                WHERE user_id = %s
                """,
                (preferences_json, now, user_id),
            )
    conn.commit()


def get_signals_and_preferences(user_id: str) -> Dict[str, Any]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT signals, preferences FROM user_preferences WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"signals": "{}", "preferences": "{}"}
    return dict(row)
