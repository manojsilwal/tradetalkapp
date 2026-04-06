"""
Shared OpenRouter API client pool: thread-safe round-robin across one or two API keys.

Used by LLMClient (chat completions) and SupabaseVectorBackend (embeddings) so both
split quota across OPENROUTER_API_KEY and optional OPENROUTER_API_KEY_2.
"""
from __future__ import annotations

import os
import threading
from typing import Any, List, Optional, Tuple

__all__ = [
    "collect_openrouter_api_keys",
    "OpenRouterClientPool",
    "get_or_create_openrouter_pool",
]


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
