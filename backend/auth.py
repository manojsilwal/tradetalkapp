"""
Authentication — Google OAuth + JWT session tokens.

Flow:
  1. Frontend sends Google ID token (from @react-oauth/google) to POST /auth/google
  2. Backend verifies it against Google's public keys
  3. Backend upserts the user in SQLite and returns a signed JWT
  4. Frontend stores JWT in localStorage and sends it as Authorization: Bearer <token>
  5. get_current_user() FastAPI dependency decodes the JWT on every protected request

Dev-mode bypass (DEV_MODE=true or GOOGLE_CLIENT_ID not set):
  POST /auth/google with {"token": "dev"} creates/returns a hardcoded dev user so
  you can work locally without a real Google account.
"""
import os
import time
import sqlite3
import threading
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Header

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
JWT_SECRET       = os.environ.get("JWT_SECRET", "dev-secret-change-in-prod")
JWT_ALGO         = "HS256"
JWT_EXPIRY_SECS  = 7 * 24 * 3600   # 7 days
DEV_MODE         = os.environ.get("DEV_MODE", "false").lower() == "true" or not GOOGLE_CLIENT_ID or GOOGLE_CLIENT_ID == "PLACEHOLDER_SET_AFTER_GOOGLE_SETUP"

# Fail-loud: warn if JWT_SECRET is the default in non-dev environments
if not DEV_MODE and JWT_SECRET == "dev-secret-change-in-prod":
    logger.critical(
        "[Auth] JWT_SECRET is the default 'dev-secret-change-in-prod' in production mode! "
        "Set JWT_SECRET environment variable to a strong random secret."
    )

DB_PATH = os.path.join(os.path.dirname(__file__), "progress.db")
_local  = threading.local()


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_users_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            name       TEXT DEFAULT '',
            avatar     TEXT DEFAULT '',
            created_at REAL DEFAULT 0
        )
    """)
    conn.commit()


@dataclass
class UserInfo:
    id:     str
    email:  str
    name:   str
    avatar: str


# ── Google token verification ─────────────────────────────────────────────────

def verify_google_token(token: str) -> dict:
    """
    Verify a Google ID token and return the claims dict.
    Raises ValueError on invalid tokens.
    """
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
        idinfo = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
        return idinfo
    except Exception as e:
        raise ValueError(f"Invalid Google token: {e}")


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _issue_jwt(user_id: str) -> str:
    try:
        import jwt
        payload = {"sub": user_id, "exp": int(time.time()) + JWT_EXPIRY_SECS}
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    except ImportError:
        if not DEV_MODE:
            raise RuntimeError(
                "PyJWT is required in production. Install it: pip install PyJWT"
            )
        import base64, json
        payload = json.dumps({"sub": user_id}).encode()
        return "dev." + base64.b64encode(payload).decode()


def _decode_jwt(token: str) -> str:
    """Returns user_id or raises ValueError."""
    if token.startswith("dev."):
        import base64, json
        try:
            payload = json.loads(base64.b64decode(token[4:]))
            return payload["sub"]
        except Exception:
            raise ValueError("Invalid dev token")
    try:
        import jwt
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload["sub"]
    except Exception as e:
        raise ValueError(f"JWT decode error: {e}")


# ── User persistence ──────────────────────────────────────────────────────────

def upsert_user(google_id: str, email: str, name: str, avatar: str) -> UserInfo:
    conn = _get_conn()
    conn.execute("""
        INSERT INTO users (id, email, name, avatar, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            email  = excluded.email,
            name   = excluded.name,
            avatar = excluded.avatar
    """, (google_id, email, name, avatar, time.time()))
    conn.commit()
    return UserInfo(id=google_id, email=email, name=name, avatar=avatar)


def get_user(user_id: str) -> Optional[UserInfo]:
    conn  = _get_conn()
    row   = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return None
    return UserInfo(id=row["id"], email=row["email"],
                    name=row["name"], avatar=row["avatar"])


# ── Public API ────────────────────────────────────────────────────────────────

def login_with_google(token: str) -> dict:
    """
    Verify a Google ID token, upsert user, return JWT + user info.
    In dev-mode, accepts token="dev" and creates a local test user.
    """
    if DEV_MODE and (token == "dev" or not GOOGLE_CLIENT_ID):
        logger.info("[Auth] DEV_MODE login — using test user")
        user = upsert_user(
            google_id="dev_user_001",
            email="dev@tradetalk.local",
            name="Dev User",
            avatar="",
        )
    else:
        try:
            claims = verify_google_token(token)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))
        user = upsert_user(
            google_id=claims["sub"],
            email=claims.get("email", ""),
            name=claims.get("name", ""),
            avatar=claims.get("picture", ""),
        )

    jwt_token = _issue_jwt(user.id)
    return {
        "token":   jwt_token,
        "user_id": user.id,
        "email":   user.email,
        "name":    user.name,
        "avatar":  user.avatar,
        "dev_mode": DEV_MODE,
    }


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def get_current_user(authorization: Optional[str] = Header(None)) -> UserInfo:
    """
    Required auth dependency — raises 401 if token missing/invalid.
    Usage: user: UserInfo = Depends(get_current_user)
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    raw_token = authorization.split(" ", 1)[1].strip()
    try:
        user_id = _decode_jwt(raw_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found — please log in again")
    return user


def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[UserInfo]:
    """
    Optional auth dependency — returns None instead of raising 401.
    Used on analysis endpoints (trace, debate, backtest) so they still
    work without auth but award XP when a logged-in user is present.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    raw_token = authorization.split(" ", 1)[1].strip()
    try:
        user_id = _decode_jwt(raw_token)
        return get_user(user_id)
    except Exception:
        return None
