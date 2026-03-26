"""
Application-level rate limiter — protects expensive LLM-backed endpoints.

Uses in-memory sliding-window counters keyed by client IP.
Not suitable for multi-worker deployments without a shared store (Redis).
"""
import os
import time
import threading
from collections import defaultdict
from fastapi import Request, HTTPException, status

RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "1").strip() == "1"

# Sliding window: {ip: [(timestamp, route_group), ...]}
_hits: dict[str, list[tuple[float, str]]] = defaultdict(list)
_lock = threading.Lock()

# Limits: (max_requests, window_seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "expensive": (10, 60),   # /trace, /debate, /analyze, /backtest
    "export":    (5, 60),    # /knowledge/export
    "default":   (60, 60),   # everything else
}


def _cleanup(ip: str, now: float):
    """Remove entries older than the largest window."""
    max_window = max(w for _, w in LIMITS.values())
    _hits[ip] = [(ts, g) for ts, g in _hits[ip] if now - ts < max_window]
    if not _hits[ip]:
        del _hits[ip]


def _check(ip: str, group: str) -> bool:
    """Return True if request is allowed."""
    if not RATE_LIMIT_ENABLED:
        return True
    now = time.monotonic()
    max_req, window = LIMITS.get(group, LIMITS["default"])
    with _lock:
        _cleanup(ip, now)
        recent = sum(1 for ts, g in _hits.get(ip, []) if g == group and now - ts < window)
        if recent >= max_req:
            return False
        _hits[ip].append((now, group))
        return True


def rate_limit(group: str = "default"):
    """FastAPI dependency factory — raises 429 when limit exceeded."""
    async def _dependency(request: Request):
        ip = request.client.host if request.client else "unknown"
        if not _check(ip, group):
            max_req, window = LIMITS.get(group, LIMITS["default"])
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: max {max_req} requests per {window}s for {group} endpoints.",
            )
    return _dependency
