"""Email OTP for two-factor sign-in (Resend)."""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import time
from typing import Optional, Tuple

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev").strip()
OTP_EXPIRY_SECS = 300
OTP_MAX_ATTEMPTS = 5


def otp_dev_mode() -> bool:
    """When true, OTP is logged locally and any 6-digit code is accepted."""
    return not RESEND_API_KEY


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _use_postgres() -> bool:
    try:
        from .postgres_config import postgres_enabled

        return postgres_enabled()
    except Exception:
        return False


def _sqlite_conn():
    from .auth import _get_conn

    return _get_conn()


def init_otp_db() -> None:
    if _use_postgres():
        from . import auth_pg

        auth_pg.init_otp_schema()
        return
    conn = _sqlite_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_otp_sessions (
            session_id  TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            otp_hash    TEXT NOT NULL,
            expires_at  REAL NOT NULL,
            attempts    INTEGER DEFAULT 0
        )
    """)
    conn.commit()


def _generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def send_otp_email(email: str, code: str) -> None:
    if otp_dev_mode():
        logger.info("[OTP] dev mode — code for %s: %s", email, code)
        return
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [email],
                "subject": "Your TradeTalk sign-in code",
                "html": (
                    f"<p>Your verification code is:</p>"
                    f"<p style='font-size:24px;font-weight:bold;letter-spacing:4px'>{code}</p>"
                    f"<p>This code expires in 5 minutes.</p>"
                ),
            },
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("[OTP] Resend send failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not send verification email. Try again.") from exc


def create_otp_session(user_id: str, email: str) -> Tuple[str, str]:
    """Create OTP session, send email. Returns (session_id, plain_otp)."""
    init_otp_db()
    session_id = secrets.token_urlsafe(24)
    plain_otp = _generate_otp()
    otp_hash = _hash_otp(plain_otp)
    expires_at = time.time() + OTP_EXPIRY_SECS

    if _use_postgres():
        from . import auth_pg

        auth_pg.insert_otp_session(session_id, user_id, otp_hash, expires_at)
    else:
        conn = _sqlite_conn()
        conn.execute(
            """
            INSERT INTO auth_otp_sessions (session_id, user_id, otp_hash, expires_at, attempts)
            VALUES (?, ?, ?, ?, 0)
            """,
            (session_id, user_id, otp_hash, expires_at),
        )
        conn.commit()

    send_otp_email(email, plain_otp)
    return session_id, plain_otp


def verify_otp(session_id: str, code: str) -> str:
    """Verify OTP and return user_id."""
    if not session_id or not code:
        raise HTTPException(status_code=400, detail="OTP session and code are required")

    init_otp_db()
    row = _fetch_otp_session(session_id)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired verification session")

    if float(row["expires_at"]) < time.time():
        _delete_otp_session(session_id)
        raise HTTPException(status_code=401, detail="Verification code expired")

    attempts = int(row.get("attempts") or 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        _delete_otp_session(session_id)
        raise HTTPException(status_code=401, detail="Too many attempts. Sign in again.")

    code_clean = code.strip()
    if not otp_dev_mode():
        if not secrets.compare_digest(_hash_otp(code_clean), row["otp_hash"]):
            _increment_attempts(session_id, attempts + 1)
            raise HTTPException(status_code=401, detail="Invalid verification code")
    elif not (code_clean.isdigit() and len(code_clean) == 6):
        raise HTTPException(status_code=401, detail="Enter a 6-digit verification code")

    user_id = row["user_id"]
    _delete_otp_session(session_id)
    return user_id


def _fetch_otp_session(session_id: str) -> Optional[dict]:
    if _use_postgres():
        from . import auth_pg

        return auth_pg.get_otp_session(session_id)
    conn = _sqlite_conn()
    row = conn.execute(
        "SELECT session_id, user_id, otp_hash, expires_at, attempts FROM auth_otp_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def _delete_otp_session(session_id: str) -> None:
    if _use_postgres():
        from . import auth_pg

        auth_pg.delete_otp_session(session_id)
        return
    conn = _sqlite_conn()
    conn.execute("DELETE FROM auth_otp_sessions WHERE session_id = ?", (session_id,))
    conn.commit()


def _increment_attempts(session_id: str, attempts: int) -> None:
    if _use_postgres():
        from . import auth_pg

        auth_pg.update_otp_attempts(session_id, attempts)
        return
    conn = _sqlite_conn()
    conn.execute(
        "UPDATE auth_otp_sessions SET attempts = ? WHERE session_id = ?",
        (attempts, session_id),
    )
    conn.commit()
