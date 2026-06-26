"""Postgres connection settings.

Two auth modes are supported:

1. Password auth (default): ``POSTGRES_PASSWORD`` from the environment.
2. Cloud SQL IAM database authentication (``POSTGRES_IAM_AUTH=1``): no password is
   stored anywhere. The process's Google service-account identity (Application
   Default Credentials) mints a short-lived OAuth2 access token that is supplied as
   the Postgres password. ``POSTGRES_USER`` must be the IAM DB username (the service
   account email *without* the ``.gserviceaccount.com`` suffix, e.g.
   ``tradetalk-etl@my-project.iam``).

On Cloud Run, connect via the Cloud SQL Auth Proxy unix socket by setting
``POSTGRES_HOST=/cloudsql/PROJECT:REGION:INSTANCE`` and attaching the instance with
``--set-cloudsql-instances``. For TCP hosts under IAM auth we force ``sslmode=require``
(Cloud SQL mandates TLS for IAM logins); for unix-socket hosts TLS is handled by the
proxy and no sslmode is added.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

from . import gcp_settings

logger = logging.getLogger(__name__)

# Cached IAM access token (tokens live ~1h; we refresh well before expiry).
_token_lock = threading.Lock()
_token_value: Optional[str] = None
_token_expiry: float = 0.0
# Documented minimal scope for Cloud SQL IAM database login.
_IAM_SCOPE = "https://www.googleapis.com/auth/sqlservice.login"


def _truthy(val: str) -> bool:
    return val.strip().lower() in {"1", "true", "yes", "on"}


def iam_auth_enabled() -> bool:
    return _truthy(os.environ.get("POSTGRES_IAM_AUTH", ""))


def _postgres_password() -> str:
    return os.environ.get("POSTGRES_PASSWORD", "").strip()


def _postgres_host() -> str:
    return os.environ.get("POSTGRES_HOST", gcp_settings.POSTGRES_HOST).strip()


def _is_unix_socket(host: str) -> bool:
    return host.startswith("/")


def _iam_access_token() -> str:
    """Return a cached/refreshed OAuth2 access token for IAM DB login.

    Uses Application Default Credentials (the Cloud Run service account in prod,
    or the developer's ``gcloud auth application-default`` login locally).
    """
    global _token_value, _token_expiry
    with _token_lock:
        now = time.time()
        if _token_value and now < _token_expiry - 300:  # refresh 5 min early
            return _token_value
        import google.auth
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default(scopes=[_IAM_SCOPE])
        creds.refresh(Request())
        _token_value = creds.token
        # creds.expiry is naive UTC; fall back to ~55 min if unknown.
        if getattr(creds, "expiry", None) is not None:
            from datetime import timezone

            _token_expiry = creds.expiry.replace(tzinfo=timezone.utc).timestamp()
        else:
            _token_expiry = now + 3300
        return _token_value


def postgres_enabled() -> bool:
    """True when portfolio (and related) should use Cloud SQL Postgres."""
    backend = os.environ.get("PORTFOLIO_STORAGE", "").strip().lower()
    if backend == "sqlite":
        return False
    if iam_auth_enabled():
        return True
    if backend == "postgres":
        return bool(_postgres_password())
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    return bool(_postgres_password() and _postgres_host())


def _auth_password() -> str:
    """Resolve the password to present to Postgres (IAM token or static password)."""
    if iam_auth_enabled():
        try:
            return _iam_access_token()
        except Exception as e:  # pragma: no cover - depends on ADC availability
            logger.error("[postgres] IAM token fetch failed: %s", e)
            raise
    return _postgres_password()


def postgres_connection_kwargs() -> Dict[str, Any]:
    """Keyword args for psycopg2.connect (no static password in source code)."""
    host = _postgres_host()
    kw: Dict[str, Any] = {
        "host": host,
        "port": int(os.environ.get("POSTGRES_PORT", gcp_settings.POSTGRES_PORT)),
        "dbname": os.environ.get("POSTGRES_DB", gcp_settings.POSTGRES_DB_NAME),
        "user": os.environ.get("POSTGRES_USER", gcp_settings.POSTGRES_USER),
        "password": _auth_password(),
        "connect_timeout": int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "10")),
    }
    # Cloud SQL requires TLS for IAM logins over TCP; the proxy handles it for sockets.
    if iam_auth_enabled() and not _is_unix_socket(host):
        kw["sslmode"] = os.environ.get("POSTGRES_SSLMODE", "require")
    return kw


def postgres_dsn() -> str:
    """Build a DSN from env or discrete fields (password never logged here)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    kw = postgres_connection_kwargs()
    user = quote_plus(str(kw["user"]))
    password = quote_plus(str(kw["password"]))
    host = kw["host"]
    port = kw["port"]
    db = kw["dbname"]
    if _is_unix_socket(host):
        # libpq unix-socket form: empty authority, socket dir via ?host=.
        return f"postgresql://{user}:{password}@/{db}?host={quote_plus(host)}&port={port}"
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    if kw.get("sslmode"):
        dsn += f"?sslmode={kw['sslmode']}"
    return dsn
