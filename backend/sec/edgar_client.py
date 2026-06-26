"""
Shared, rate-limited async SEC EDGAR client.

SEC fair-access policy requires a descriptive User-Agent and limits automated
access to <= 10 requests/second. This module funnels ALL EDGAR traffic through a
single process-wide token-bucket limiter (default ~6 req/s) so concurrent callers
(universe builder, per-filer ingestion, downloads) never collectively exceed the
cap. It also:
- sets a real User-Agent from SEC_USER_AGENT,
- never hardcodes Host (httpx derives it: data.sec.gov vs www.sec.gov),
- follows redirects (padded-CIK archive paths 301-redirect),
- retries 429/503 with backoff, honoring Retry-After.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


def _default_user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", "TradeTalkApp contact@tradetalk.example.com").strip()


def _rate_limit_rps() -> float:
    try:
        return float(os.environ.get("SEC_RATE_LIMIT_RPS", "6"))
    except ValueError:
        return 6.0


_MAX_RETRIES = int(os.environ.get("SEC_MAX_RETRIES", "4"))


class _RateLimiter:
    """Process-wide minimum-interval limiter (async-safe)."""

    def __init__(self, rps: float):
        self._min_interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self._min_interval


class EdgarClient:
    """Singleton-style async client. Use ``edgar`` module-level instance."""

    def __init__(self):
        self._limiter = _RateLimiter(_rate_limit_rps())
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=httpx.Timeout(60.0),
                        follow_redirects=True,
                        headers={
                            "User-Agent": _default_user_agent(),
                            "Accept-Encoding": "gzip, deflate",
                        },
                    )
        return self._client

    async def _request(self, url: str, *, stream_to: Optional[Path] = None) -> httpx.Response:
        client = await self._get_client()
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            await self._limiter.acquire()
            try:
                resp = await client.get(url)
                if resp.status_code in (429, 503):
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if (retry_after and retry_after.isdigit()) else (2.0 ** attempt)
                    logger.warning("[EDGAR] %s -> %s, backing off %.1fs", url, resp.status_code, delay)
                    await asyncio.sleep(min(delay, 30.0))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError:
                raise
            except Exception as e:  # network/timeout — retry
                last_exc = e
                await asyncio.sleep(2.0 ** attempt)
        if last_exc:
            raise last_exc
        raise httpx.HTTPError(f"[EDGAR] exhausted retries for {url}")

    async def get_json(self, url: str) -> Any:
        resp = await self._request(url)
        return resp.json()

    async def get_text(self, url: str) -> str:
        resp = await self._request(url)
        return resp.text

    async def get_bytes(self, url: str) -> bytes:
        resp = await self._request(url)
        return resp.content

    async def download_to(self, url: str, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        content = await self.get_bytes(url)
        out_path.write_bytes(content)
        return out_path

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Module-level shared instance.
edgar = EdgarClient()
