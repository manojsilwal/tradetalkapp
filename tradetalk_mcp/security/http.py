"""Restricted HTTP client for OpenAPI and allowlisted API actions."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin, urlparse


class HttpPolicyError(ValueError):
    pass


def _host_allowed(url: str, allowlist: frozenset[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and host in allowlist


def fetch_url(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_sec: float = 60.0,
    host_allowlist: frozenset[str],
) -> tuple[int, str]:
    if not _host_allowed(url, host_allowlist):
        raise HttpPolicyError(f"host not in allowlist for URL: {url}")
    req = urllib.request.Request(url, data=body, method=method.upper())
    req.add_header("Accept", "application/json, */*")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, raw
    except urllib.error.URLError as e:
        raise HttpPolicyError(f"request failed: {e}") from e


def fetch_openapi(openapi_url: str, host_allowlist: frozenset[str]) -> str:
    code, text = fetch_url(openapi_url, method="GET", host_allowlist=host_allowlist)
    if code >= 400:
        raise HttpPolicyError(f"OpenAPI fetch HTTP {code}: {text[:500]}")
    return text


def api_request(
    base_url: str,
    path: str,
    *,
    method: str,
    json_body: dict[str, Any] | None,
    api_key: str,
    host_allowlist: frozenset[str],
) -> tuple[int, str]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if not _host_allowed(url, host_allowlist):
        raise HttpPolicyError(f"resolved host not allowed: {url}")
    body_bytes = None
    hdrs: dict[str, str] = {}
    if api_key:
        hdrs["X-TradeTalk-MCP-Key"] = api_key
    if json_body is not None and method.upper() != "GET":
        body_bytes = json.dumps(json_body).encode("utf-8")
    return fetch_url(url, method=method, body=body_bytes, headers=hdrs or None, host_allowlist=host_allowlist)


class RateLimiter:
    """Simple per-key minimum interval (wall clock)."""

    def __init__(self, min_interval_sec: float) -> None:
        self._min = min_interval_sec
        self._last: dict[str, float] = {}

    def wait(self, key: str) -> None:
        if self._min <= 0:
            return
        now = time.monotonic()
        last = self._last.get(key, 0.0)
        delta = now - last
        if delta < self._min:
            time.sleep(self._min - delta)
        self._last[key] = time.monotonic()
