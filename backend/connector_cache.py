"""
connector_cache.py
------------------
Lightweight TTL cache for connector fetch results.

Each connector can call ``get_cached`` / ``set_cached`` to avoid
re-fetching identical data within a short window (default 5 min).
"""
import os
import time
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL: int = 300  # seconds
_OPEN_SESSION_TTL: int = int(os.environ.get("CONNECTOR_CACHE_OPEN_TTL_S", "60"))

_store: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def connector_cache_ttl(default_ttl: int = _DEFAULT_TTL) -> int:
    """Shorter TTL during regular session; longer off-hours."""
    try:
        from .market_calendar import SESSION_REGULAR, session_status

        if session_status() == SESSION_REGULAR:
            return _OPEN_SESSION_TTL
    except Exception:
        pass
    return default_ttl


def _key(connector: str, ticker: str) -> str:
    return f"{connector}::{ticker.upper()}"


def get_cached(connector: str, ticker: str, ttl: Optional[int] = None) -> Optional[Any]:
    """Return cached result if present and fresh, else ``None``."""
    effective_ttl = connector_cache_ttl() if ttl is None else ttl
    k = _key(connector, ticker)
    with _lock:
        entry = _store.get(k)
        if entry is None:
            return None
        ts, value = entry
        if time.time() - ts > effective_ttl:
            del _store[k]
            return None
        logger.debug("[ConnectorCache] HIT %s", k)
        return value


def set_cached(connector: str, value: Any, ticker: str) -> None:
    """Store a connector result with the current timestamp, cleaning up expired or excess items."""
    k = _key(connector, ticker)
    now = time.time()
    with _lock:
        # Prevent unbounded memory growth by enforcing a capacity limit (e.g., 1000 items)
        if len(_store) >= 1000:
            # Evict expired entries first
            effective_ttl = connector_cache_ttl()
            expired_keys = [
                key for key, (ts, _) in _store.items()
                if now - ts > effective_ttl
            ]
            for key in expired_keys:
                del _store[key]

            # If still exceeding capacity, evict the oldest entries
            if len(_store) >= 1000:
                # Sort keys by timestamp ascending (oldest first)
                sorted_keys = sorted(_store.keys(), key=lambda x: _store[x][0])
                # Evict oldest 100 entries to restore safety margin
                for old_key in sorted_keys[:100]:
                    del _store[old_key]

        _store[k] = (now, value)
    logger.debug("[ConnectorCache] SET %s", k)

