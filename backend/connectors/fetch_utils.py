"""
Shared HTTP helpers — exponential backoff, jitter, and cursor/offset pagination.

Used by prediction-market connectors and other rate-limited free APIs.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, TypeVar

import requests

logger = logging.getLogger(__name__)

T = TypeVar("T")


def sleep_backoff(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> None:
    """Exponential backoff with small jitter (attempt is 0-based)."""
    delay = min(cap, base * (2**attempt)) + random.uniform(0.0, 0.25)
    time.sleep(delay)


def request_with_backoff(
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    timeout: float = 10.0,
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504}),
    **kwargs: Any,
) -> requests.Response:
    """HTTP request with retries on transient failures and 429."""
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in retry_statuses and attempt < max_retries - 1:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(30.0, float(retry_after)))
                else:
                    sleep_backoff(attempt)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= max_retries - 1:
                raise
            sleep_backoff(attempt)
    assert last_exc is not None
    raise last_exc


def paginate_cursor(
    fetch_page: Callable[[Optional[str]], tuple[List[T], Optional[str]]],
    *,
    max_pages: int = 5,
    inter_page_delay: float = 0.15,
) -> List[T]:
    """
    Walk cursor-based APIs (Kalshi ``cursor``, Polymarket keyset ``next_cursor``).

    ``fetch_page(cursor)`` returns ``(items, next_cursor)``. Stops when
    ``next_cursor`` is empty or ``max_pages`` is reached.
    """
    out: List[T] = []
    cursor: Optional[str] = None
    for page_idx in range(max_pages):
        batch, cursor = fetch_page(cursor)
        if batch:
            out.extend(batch)
        if not cursor:
            break
        if page_idx < max_pages - 1 and inter_page_delay > 0:
            time.sleep(inter_page_delay)
    return out


def paginate_offset(
    fetch_page: Callable[[int], List[T]],
    *,
    page_size: int = 100,
    max_pages: int = 5,
    inter_page_delay: float = 0.15,
) -> List[T]:
    """Offset/limit pagination fallback when cursor APIs are unavailable."""
    out: List[T] = []
    for page_idx in range(max_pages):
        batch = fetch_page(page_idx * page_size)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page_size:
            break
        if page_idx < max_pages - 1 and inter_page_delay > 0:
            time.sleep(inter_page_delay)
    return out
