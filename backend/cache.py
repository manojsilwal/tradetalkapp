"""
cache.py
---------
L1 in-memory tool-call cache with LRU eviction.

Usage:
    from .cache import cached_tool_call

    result = cached_tool_call("fetch_fundamentals", {"ticker": "AAPL"}, lambda: fetch_fundamentals("AAPL"))

Cache is scoped to the process lifetime (per-worker).
Cap: 100 entries — oldest evicted first (LRU via OrderedDict).
This makes repeated sub-queries within one conversation completely free (no network/DB round-trip).
"""
import hashlib
import logging
from collections import OrderedDict
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
_MAX_ENTRIES: int = 100

# OrderedDict maintains insertion order → move-to-end on hit → oldest auto-evicted
_tool_cache: OrderedDict = OrderedDict()
_hits: int = 0
_misses: int = 0


def _make_key(tool_name: str, args: Any) -> str:
    """Stable MD5 key from tool name + args."""
    raw = f"{tool_name}::{args!r}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cached_tool_call(tool_name: str, args: Any, fn: Callable[[], Any]) -> Any:
    """
    Return cached result for (tool_name, args) if present; otherwise call fn(),
    store the result, and return it.

    Args:
        tool_name: Logical name of the tool (used in logging/key).
        args:      Arguments dict or any hashable-repr value to key on.
        fn:        Zero-arg callable that computes the result on cache miss.

    Returns:
        The cached or freshly-computed result.
    """
    global _hits, _misses

    key = _make_key(tool_name, args)

    if key in _tool_cache:
        # Move to end → mark as recently used
        _tool_cache.move_to_end(key)
        _hits += 1
        logger.debug("[Cache] HIT  tool=%s  hits=%d  misses=%d", tool_name, _hits, _misses)
        return _tool_cache[key]

    # Cache miss — compute
    _misses += 1
    result = fn()
    _tool_cache[key] = result
    _tool_cache.move_to_end(key)

    # Evict oldest if over cap
    while len(_tool_cache) > _MAX_ENTRIES:
        evicted_key, _ = _tool_cache.popitem(last=False)
        logger.debug("[Cache] EVICT key=%s", evicted_key[:8])

    logger.debug("[Cache] MISS tool=%s  hits=%d  misses=%d  size=%d",
                 tool_name, _hits, _misses, len(_tool_cache))
    return result


def cache_stats() -> dict:
    """Return cache hit/miss counts and current size."""
    return {
        "hits": _hits,
        "misses": _misses,
        "size": len(_tool_cache),
        "max_entries": _MAX_ENTRIES,
        "hit_rate_pct": round(_hits / max(1, _hits + _misses) * 100, 1),
    }


def invalidate(tool_name: str | None = None) -> int:
    """
    Flush cache entries.
    - If tool_name is given: remove only entries for that tool.
    - Otherwise: clear everything.
    Returns number of entries removed.
    """
    global _hits, _misses
    if tool_name is None:
        count = len(_tool_cache)
        _tool_cache.clear()
        _hits = _misses = 0
        logger.info("[Cache] Full flush — removed %d entries", count)
        return count

    prefix = hashlib.md5(f"{tool_name}::".encode()).hexdigest()[:8]
    targets = [k for k in _tool_cache if k.startswith(prefix)]  # approximate match
    for k in targets:
        del _tool_cache[k]
    logger.info("[Cache] Partial flush — tool=%s  removed=%d", tool_name, len(targets))
    return len(targets)
