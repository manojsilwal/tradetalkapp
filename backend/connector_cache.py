"""
connector_cache.py
------------------
Lightweight TTL cache for connector fetch results.

Each connector can call ``get_cached`` / ``set_cached`` to avoid
re-fetching identical data within a short window (default 5 min).
"""
import time
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL: int = 300  # seconds

_store: dict[str, tuple[float, Any]] = {}


def _key(connector: str, ticker: str) -> str:
    return f"{connector}::{ticker.upper()}"


def get_cached(connector: str, ticker: str, ttl: int = _DEFAULT_TTL) -> Optional[Any]:
    """Return cached result if present and fresh, else ``None``."""
    k = _key(connector, ticker)
    entry = _store.get(k)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        del _store[k]
        return None
    logger.debug("[ConnectorCache] HIT %s", k)
    return value


def set_cached(connector: str, value: Any, ticker: str) -> None:
    """Store a connector result with the current timestamp."""
    k = _key(connector, ticker)
    _store[k] = (time.time(), value)
    logger.debug("[ConnectorCache] SET %s", k)
