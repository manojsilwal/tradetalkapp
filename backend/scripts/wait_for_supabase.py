#!/usr/bin/env python3
"""
Poll Supabase until the REST API responds (wakes free-tier paused projects), then
optionally verify the pgvector bootstrap was applied (``public.vector_memory``).

Supabase free projects pause after idle time; the first requests can return 5xx
or connection errors until the project finishes resuming.

Environment (required):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Optional:
  WAIT_MAX_SECONDS          — total wall-clock budget for wake loop (default 900)
  WAIT_INTERVAL_SECONDS     — sleep between wake attempts (default 20)
  SKIP_VECTOR_MEMORY_CHECK  — if ``1``, skip table check (not recommended for batch ETL)
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict

import requests


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return int(str(raw).strip())


def _bootstrap_help() -> str:
    return (
        "The table public.vector_memory is missing (PostgREST PGRST205 / HTTP 404).\n"
        "One-time fix: Supabase Dashboard → SQL Editor → paste and run the full contents of:\n"
        "  https://github.com/manojsilwal/tradetalkapp/blob/main/backend/supabase_pgvector_bootstrap.sql\n"
        "or open backend/supabase_pgvector_bootstrap.sql in this repo and run it in SQL Editor.\n"
        "That creates vector_memory + match_vector_memory required for VECTOR_BACKEND=supabase and batch ETL."
    )


def wait_for_rest_api(url: str, headers: dict[str, str], *, max_wait: int, interval: int) -> int:
    """Poll GET /rest/v1/ until non-5xx (or 401 = bad key). Returns 0 on success."""
    deadline = time.monotonic() + max_wait
    probe = f"{url}/rest/v1/"
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            r = requests.get(probe, headers=headers, timeout=45)
            if r.status_code == 401:
                print(
                    "wait_for_supabase: HTTP 401 — check SUPABASE_SERVICE_ROLE_KEY",
                    file=sys.stderr,
                )
                return 1
            if r.status_code < 500:
                print(
                    f"wait_for_supabase: API reachable after {attempt} attempt(s) (HTTP {r.status_code})"
                )
                return 0
            print(
                f"wait_for_supabase: attempt {attempt} HTTP {r.status_code}, "
                f"retrying in {interval}s (free tier may be resuming)…"
            )
        except requests.RequestException as e:
            print(
                f"wait_for_supabase: attempt {attempt} error {e!r}, "
                f"retrying in {interval}s…"
            )
        time.sleep(interval)

    print(
        f"wait_for_supabase: timed out after {max_wait}s — project may still be paused. "
        "Open the Supabase dashboard to wake it, or increase WAIT_MAX_SECONDS.",
        file=sys.stderr,
    )
    return 1


def verify_vector_memory(url: str, headers: dict[str, str]) -> int:
    """
    Ensure ``vector_memory`` exists (batch ETL + RAG). Retries 5xx a few times;
    missing table (404 / PGRST205) fails immediately with bootstrap instructions.
    """
    probe = f"{url}/rest/v1/vector_memory?select=id&limit=1"
    max_attempts = 8
    interval = 10
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(probe, headers=headers, timeout=45)
        except requests.RequestException as e:
            print(f"wait_for_supabase: vector_memory probe attempt {attempt}: {e!r}")
            if attempt >= max_attempts:
                return 1
            time.sleep(interval)
            continue

        if r.status_code == 200:
            print("wait_for_supabase: public.vector_memory is present (pgvector bootstrap OK)")
            return 0

        if r.status_code == 401:
            print(
                "wait_for_supabase: HTTP 401 on vector_memory — check SUPABASE_SERVICE_ROLE_KEY",
                file=sys.stderr,
            )
            return 1

        if r.status_code == 404:
            try:
                body: Dict[str, Any] = r.json()
            except Exception:
                body = {}
            code = body.get("code", "")
            if code == "PGRST205" or "vector_memory" in (body.get("message") or "").lower():
                print("wait_for_supabase: " + _bootstrap_help(), file=sys.stderr)
                print(
                    "::error::Supabase pgvector bootstrap not applied — run backend/supabase_pgvector_bootstrap.sql in SQL Editor",
                    file=sys.stderr,
                )
                return 1

        if r.status_code >= 500:
            print(
                f"wait_for_supabase: vector_memory probe HTTP {r.status_code}, "
                f"retry {attempt}/{max_attempts} in {interval}s…"
            )
            time.sleep(interval)
            continue

        print(
            f"wait_for_supabase: unexpected HTTP {r.status_code} for vector_memory: {r.text[:500]}",
            file=sys.stderr,
        )
        return 1

    print(
        "wait_for_supabase: vector_memory probe failed after retries (server errors).",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        print(
            "wait_for_supabase: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set",
            file=sys.stderr,
        )
        return 1

    max_wait = max(30, _int_env("WAIT_MAX_SECONDS", 900))
    interval = max(5, _int_env("WAIT_INTERVAL_SECONDS", 20))
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    rc = wait_for_rest_api(url, headers, max_wait=max_wait, interval=interval)
    if rc != 0:
        return rc

    if os.environ.get("SKIP_VECTOR_MEMORY_CHECK", "").strip() in ("1", "true", "yes"):
        print("wait_for_supabase: SKIP_VECTOR_MEMORY_CHECK set — skipping vector_memory check")
        return 0

    return verify_vector_memory(url, headers)


if __name__ == "__main__":
    sys.exit(main())
