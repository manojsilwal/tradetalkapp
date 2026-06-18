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
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Header

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
JWT_SECRET       = os.environ.get("JWT_SECRET", "dev-secret-change-in-prod")
JWT_ALGO         = "HS256"
JWT_EXPIRY_SECS  = 7 * 24 * 3600   # 7 days
SETUP_JWT_EXPIRY_SECS = 15 * 60    # 15 minutes for post-Google password setup
DEV_MODE         = os.environ.get("DEV_MODE", "false").lower() == "true" or not GOOGLE_CLIENT_ID or GOOGLE_CLIENT_ID == "PLACEHOLDER_SET_AFTER_GOOGLE_SETUP"

_DEFAULT_ADMIN_EMAIL = "silwal.saroj44@gmail.com"
ADMIN_EMAILS = frozenset(
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", _DEFAULT_ADMIN_EMAIL).split(",")
    if e.strip()
)

# Fail-loud: raise error if JWT_SECRET is the default in non-dev environments
if not DEV_MODE and JWT_SECRET == "dev-secret-change-in-prod":
    raise RuntimeError(
        "[Auth] JWT_SECRET is the default 'dev-secret-change-in-prod' in production mode! "
        "Set JWT_SECRET environment variable to a strong random secret."
    )

from .progress_db import resolve_progress_db_path

DB_PATH = resolve_progress_db_path()
_local  = threading.local()


def _use_postgres() -> bool:
    try:
        from .postgres_config import postgres_enabled

        return postgres_enabled()
    except Exception:
        return False


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return f"pbkdf2_sha256$100000${salt}${key.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations, salt, key_hex = password_hash.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        key = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            int(iterations)
        )
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def init_users_db():
    if _use_postgres():
        from . import auth_pg

        auth_pg.init_schema()
        auth_pg.migrate_from_sqlite_if_needed()
        return
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            email         TEXT NOT NULL,
            name          TEXT DEFAULT '',
            avatar        TEXT DEFAULT '',
            password_hash TEXT DEFAULT NULL,
            created_at    REAL DEFAULT 0
        )
    """)
    conn.commit()

    # Alter table if password_hash does not exist (for existing tables)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT DEFAULT NULL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # already exists

    # Ensure unique index on email
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    from .email_otp import init_otp_db

    init_otp_db()


@dataclass
class UserInfo:
    id:     str
    email:  str
    name:   str
    avatar: str


def user_is_admin(user: UserInfo) -> bool:
    """True when the user's email is in the admin allowlist (single admin by default)."""
    return user.email.strip().lower() in ADMIN_EMAILS


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


def _issue_setup_jwt(user_id: str) -> str:
    try:
        import jwt
        payload = {
            "sub": user_id,
            "purpose": "password_setup",
            "exp": int(time.time()) + SETUP_JWT_EXPIRY_SECS,
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    except ImportError:
        if not DEV_MODE:
            raise RuntimeError("PyJWT is required in production.")
        import base64, json
        payload = json.dumps({"sub": user_id, "purpose": "password_setup"}).encode()
        return "setup." + base64.b64encode(payload).decode()


def _decode_setup_jwt(token: str) -> str:
    if token.startswith("setup."):
        import base64, json
        try:
            payload = json.loads(base64.b64decode(token[6:]))
            if payload.get("purpose") != "password_setup":
                raise ValueError("Invalid setup token")
            return payload["sub"]
        except Exception:
            raise ValueError("Invalid setup token")
    try:
        import jwt
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        if payload.get("purpose") != "password_setup":
            raise ValueError("Invalid setup token")
        return payload["sub"]
    except Exception as e:
        raise ValueError(f"Setup token error: {e}")


# ── User persistence ──────────────────────────────────────────────────────────

def upsert_user(google_id: str, email: str, name: str, avatar: str) -> UserInfo:
    if _use_postgres():
        from . import auth_pg

        row = auth_pg.upsert_user(google_id, email, name, avatar)
        return UserInfo(id=row["id"], email=row["email"], name=row["name"], avatar=row["avatar"])
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
    if _use_postgres():
        from . import auth_pg

        row = auth_pg.get_user(user_id)
        if not row:
            return None
        return UserInfo(
            id=row["id"], email=row["email"], name=row["name"], avatar=row["avatar"]
        )
    conn  = _get_conn()
    row   = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return None
    return UserInfo(id=row["id"], email=row["email"],
                    name=row["name"], avatar=row["avatar"])


def _get_auth_row_by_email(email: str) -> Optional[dict]:
    if _use_postgres():
        from . import auth_pg

        return auth_pg.get_user_by_email(email)
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
    return dict(row) if row else None


def _get_auth_row_by_id(user_id: str) -> Optional[dict]:
    if _use_postgres():
        from . import auth_pg

        return auth_pg.get_user_auth_row(user_id)
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def user_has_password(user_id: str) -> bool:
    row = _get_auth_row_by_id(user_id)
    return bool(row and row.get("password_hash"))


def set_user_password(user_id: str, password: str) -> None:
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
    pw_hash = hash_password(password)
    if _use_postgres():
        from . import auth_pg

        auth_pg.set_user_password(user_id, pw_hash)
        return
    conn = _get_conn()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
    conn.commit()


def _user_profile_payload(user: UserInfo) -> dict:
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar": user.avatar,
        "dev_mode": DEV_MODE,
        "has_password": user_has_password(user.id),
        "is_admin": user_is_admin(user),
    }


def _user_session_payload(user: UserInfo) -> dict:
    return {
        "token": _issue_jwt(user.id),
        **_user_profile_payload(user),
    }


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

    return _user_session_payload(user)


def create_manual_user(email: str, password: str, name: str = "") -> dict:
    email_clean = email.strip().lower()
    if not email_clean or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
    user_id = f"manual_user_{int(time.time())}_{secrets.token_hex(4)}"
    pw_hash = hash_password(password)
    user_name = name.strip() or email_clean.split('@')[0]

    if _use_postgres():
        from . import auth_pg

        if auth_pg.email_exists(email_clean):
            raise HTTPException(status_code=400, detail="A user with this email is already registered")
        auth_pg.create_manual_user(user_id, email_clean, user_name, pw_hash)
    else:
        conn = _get_conn()
        existing = conn.execute(
            "SELECT id FROM users WHERE LOWER(email) = ?", (email_clean,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="A user with this email is already registered")
        conn.execute("""
            INSERT INTO users (id, email, name, avatar, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, email_clean, user_name, "", pw_hash, time.time()))
        conn.commit()
    
    user = get_user(user_id)
    if not user:
        user = UserInfo(id=user_id, email=email_clean, name=user_name, avatar="")
    return _user_session_payload(user)


def _google_user_from_token(token: str) -> UserInfo:
    if DEV_MODE and (token == "dev" or not GOOGLE_CLIENT_ID):
        logger.info("[Auth] DEV_MODE Google signup — using test user")
        return upsert_user(
            google_id="dev_user_001",
            email="dev@tradetalk.local",
            name="Dev User",
            avatar="",
        )
    try:
        claims = verify_google_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return upsert_user(
        google_id=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name", ""),
        avatar=claims.get("picture", ""),
    )


def signup_with_google(token: str) -> dict:
    """Google signup — upsert user and return setup token if password not yet set."""
    user = _google_user_from_token(token)
    if user_has_password(user.id):
        raise HTTPException(
            status_code=409,
            detail="Account already exists. Please sign in with your email and password.",
        )
    return {
        "needs_password": True,
        "setup_token": _issue_setup_jwt(user.id),
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar": user.avatar,
        "dev_mode": DEV_MODE,
    }


def complete_set_password(setup_token: str, password: str) -> dict:
    try:
        user_id = _decode_setup_jwt(setup_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    set_user_password(user_id, password)
    return {
        "status": "password_set",
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
    }


def initiate_login_with_password(email: str, password: str) -> dict:
    """Verify email/password and start email OTP sign-in."""
    from .email_otp import create_otp_session, otp_dev_mode

    email_clean = email.strip().lower()
    if not email_clean or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    row = _get_auth_row_by_email(email_clean)
    if not row or not row.get("password_hash") or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    session_id, _plain = create_otp_session(row["id"], row["email"])
    return {
        "otp_session_id": session_id,
        "email": row["email"],
        "dev_mode": DEV_MODE,
        "otp_dev_bypass": otp_dev_mode(),
    }


def complete_login_with_otp(otp_session_id: str, code: str) -> dict:
    """Verify OTP and issue session JWT."""
    from .email_otp import verify_otp

    user_id = verify_otp(otp_session_id, code)
    user = get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return _user_session_payload(user)

def login_with_password(email: str, password: str) -> dict:
    """Legacy alias — initiates OTP login (step 1)."""
    return initiate_login_with_password(email, password)


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


def get_current_admin_user(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """Required admin dependency — raises 403 unless user email is in ADMIN_EMAILS."""
    if not user_is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
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


def get_current_user_or_dev(authorization: Optional[str] = Header(None)) -> UserInfo:
    """
    Portfolio-friendly auth: use JWT when present; in DEV_MODE fall back to the
    local dev user so paper portfolio works without a sign-in wall.
    """
    user = get_optional_user(authorization)
    if user:
        return user
    if DEV_MODE:
        existing = get_user("dev_user_001")
        if existing:
            return existing
        return upsert_user(
            google_id="dev_user_001",
            email="dev@tradetalk.local",
            name="Dev User",
            avatar="",
        )
    raise HTTPException(status_code=401, detail="Missing Authorization header")
