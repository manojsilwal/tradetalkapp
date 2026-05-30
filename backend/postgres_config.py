"""Postgres connection settings (password from environment only)."""
from __future__ import annotations

import os
from typing import Any, Dict
from urllib.parse import quote_plus

from . import gcp_settings


def postgres_enabled() -> bool:
    """True when portfolio (and related) should use Cloud SQL Postgres."""
    backend = os.environ.get("PORTFOLIO_STORAGE", "").strip().lower()
    if backend == "sqlite":
        return False
    if backend == "postgres":
        return bool(_postgres_password())
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    return bool(_postgres_password() and _postgres_host())


def _postgres_password() -> str:
    return os.environ.get("POSTGRES_PASSWORD", "").strip()


def _postgres_host() -> str:
    return os.environ.get("POSTGRES_HOST", gcp_settings.POSTGRES_HOST).strip()


def postgres_connection_kwargs() -> Dict[str, Any]:
    """Keyword args for psycopg2.connect (no password in source code)."""
    return {
        "host": _postgres_host(),
        "port": int(os.environ.get("POSTGRES_PORT", gcp_settings.POSTGRES_PORT)),
        "dbname": os.environ.get("POSTGRES_DB", gcp_settings.POSTGRES_DB_NAME),
        "user": os.environ.get("POSTGRES_USER", gcp_settings.POSTGRES_USER),
        "password": _postgres_password(),
        "connect_timeout": int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "10")),
    }


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
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"
