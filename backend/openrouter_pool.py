"""
Shared OpenRouter API client pool: thread-safe round-robin across one or two API keys.

Used by LLMClient (chat completions) and SupabaseVectorBackend (embeddings).
With two keys, each request normally uses one key chosen in round-robin order
(``sync_clients_for_request`` / ``async_clients_for_request`` with
``OPENROUTER_429_TRY_OTHER_KEYS=0``). Set ``OPENROUTER_429_TRY_OTHER_KEYS=1`` to also
pass the other key(s) for retry after 429 on the first key.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

__all__ = [
    "collect_openrouter_api_keys",
    "OpenRouterClientPool",
    "get_or_create_openrouter_pool",
    "is_openrouter_rate_limit_error",
    "rate_limit_sleep_seconds",
    "sync_failover_execute",
    "should_try_other_openrouter_keys_on_429",
]


def should_try_other_openrouter_keys_on_429() -> bool:
    """
    When True, LLM/embed paths receive all keys (rotating primary) for 429 failover.
    When False (default), only the round-robin primary client is used per request —
    both accounts still share load across successive requests.
    """
    v = os.environ.get("OPENROUTER_429_TRY_OTHER_KEYS", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def collect_openrouter_api_keys() -> List[str]:
    """Return non-empty keys in order: primary, then optional second account."""
    keys: List[str] = []
    k1 = os.environ.get("OPENROUTER_API_KEY", "").strip()
    k2 = os.environ.get("OPENROUTER_API_KEY_2", "").strip()
    if k1:
        keys.append(k1)
    if k2:
        keys.append(k2)
    return keys


def is_openrouter_rate_limit_error(exc: BaseException) -> bool:
    """True for HTTP 429 / upstream rate-limit messages from OpenRouter or providers."""
    try:
        from openai import RateLimitError

        if isinstance(exc, RateLimitError):
            return True
    except ImportError:
        pass
    code = getattr(exc, "status_code", None)
    if code == 429:
        return True
    s = str(exc).lower()
    if "429" in s:
        return True
    if "rate" in s and ("limit" in s or "limited" in s):
        return True
    return False


def rate_limit_sleep_seconds(exc: BaseException, default_sec: float = 2.5) -> float:
    """
    Seconds to wait after a rate limit before retry (Retry-After header if present).
    Bounded to [0.5, 90] to avoid stalling the chat UI.
    """
    resp = getattr(exc, "response", None)
    if resp is not None:
        h = getattr(resp, "headers", None)
        if h:
            raw = h.get("retry-after") or h.get("Retry-After")
            if raw is not None:
                try:
                    return min(90.0, max(0.5, float(raw)))
                except (TypeError, ValueError):
                    pass
    return min(90.0, max(0.5, float(default_sec)))


def sync_failover_execute(
    clients: List[Any],
    fn: Callable[[Any], Any],
    *,
    default_same_key_delay: float = 2.5,
    default_key_failover_delay: float = 1.0,
    exit_immediately_on_rate_limit: bool = False,
) -> Tuple[Optional[Any], Optional[BaseException]]:
    """
    Run fn(client) for each OpenRouter sync client: up to two attempts per key on 429,
    then optional sleep and failover to the next key. Honors OPENROUTER_429_* env delays.

    If ``exit_immediately_on_rate_limit`` is True, the first 429 returns immediately with no
    sleeps or further OpenRouter attempts (caller can fail over to another provider).

    Returns (result, None) on success. On non-429 errors, returns (None, exc) immediately.
    If all keys fail with 429, returns (None, last_rate_limit_exception).
    """
    if not clients:
        return None, None
    same = float(os.environ.get("OPENROUTER_429_SAME_KEY_DELAY_SEC", str(default_same_key_delay)))
    key_d = float(os.environ.get("OPENROUTER_429_KEY_FAILOVER_DELAY_SEC", str(default_key_failover_delay)))
    n = len(clients)
    ci = 0
    last_rl: Optional[BaseException] = None
    while ci < n:
        client = clients[ci]
        for attempt in range(2):
            try:
                return fn(client), None
            except BaseException as e:
                if not is_openrouter_rate_limit_error(e):
                    return None, e
                last_rl = e
                if exit_immediately_on_rate_limit:
                    return None, e
                wait = rate_limit_sleep_seconds(e, same)
                if attempt == 0:
                    time.sleep(wait)
                    continue
                if ci < n - 1:
                    time.sleep(key_d)
                    break
                return None, e
        ci += 1
    return None, last_rl


class OpenRouterClientPool:
    """Paired OpenAI + AsyncOpenAI clients per key; round-robin via next_pair / next_sync."""

    def __init__(self, base_url: str, headers: dict, keys: List[str]) -> None:
        if not keys:
            raise ValueError("OpenRouterClientPool requires at least one API key")
        from openai import AsyncOpenAI, OpenAI

        self._pairs: List[Tuple[Any, Any]] = []
        for api_key in keys:
            self._pairs.append(
                (
                    OpenAI(
                        base_url=base_url,
                        api_key=api_key,
                        default_headers=headers,
                    ),
                    AsyncOpenAI(
                        base_url=base_url,
                        api_key=api_key,
                        default_headers=headers,
                    ),
                )
            )
        self._lock = threading.Lock()
        self._idx = 0

    def next_pair(self) -> Tuple[Any, Any]:
        with self._lock:
            i = self._idx % len(self._pairs)
            self._idx += 1
            return self._pairs[i]

    def next_sync(self) -> Any:
        return self.next_pair()[0]

    def next_async(self) -> Any:
        return self.next_pair()[1]

    def sync_clients_for_request(self, include_other_keys_for_429: bool) -> List[Any]:
        """
        Clients for one logical API call. Advances the round-robin index once.

        If ``include_other_keys_for_429`` is False, returns a single sync client
        (strict rotation across accounts). If True, returns all keys with the
        round-robin primary first (for 429 retry on the next key).
        """
        if include_other_keys_for_429:
            return self.failover_sync_clients()
        return [self.next_sync()]

    def async_clients_for_request(self, include_other_keys_for_429: bool) -> List[Any]:
        """Same as ``sync_clients_for_request`` for async clients."""
        if include_other_keys_for_429:
            return self.failover_async_clients()
        return [self.next_async()]

    def failover_sync_clients(self) -> List[Any]:
        """
        Sync OpenAI clients for one logical request: round-robin primary, then other key(s)
        for 429 fallback when OPENROUTER_API_KEY_2 is set.
        """
        with self._lock:
            n = len(self._pairs)
            if n == 0:
                return []
            primary = self._idx % n
            self._idx += 1
            return [self._pairs[(primary + i) % n][0] for i in range(n)]

    def failover_async_clients(self) -> List[Any]:
        """Async clients in the same order as failover_sync_clients (one idx advance)."""
        with self._lock:
            n = len(self._pairs)
            if n == 0:
                return []
            primary = self._idx % n
            self._idx += 1
            return [self._pairs[(primary + i) % n][1] for i in range(n)]


_pool: Optional[OpenRouterClientPool] = None
_pool_lock = threading.Lock()


def get_or_create_openrouter_pool(base_url: str, headers: dict) -> Optional[OpenRouterClientPool]:
    """Singleton pool so LLM and embeddings share one round-robin counter."""
    global _pool
    keys = collect_openrouter_api_keys()
    if not keys:
        return None
    with _pool_lock:
        if _pool is None:
            _pool = OpenRouterClientPool(base_url, headers, keys)
        return _pool
