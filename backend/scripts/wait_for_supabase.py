#!/usr/bin/env python3
"""
Poll Supabase until the REST API responds (wakes free-tier paused projects).

Supabase free projects pause after idle time; the first requests can return 5xx
or connection errors until the project finishes resuming. Use this in CI
before batch ETL or any job that needs Postgres + PostgREST.

Environment (required):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Optional:
  WAIT_MAX_SECONDS     — total wall-clock budget (default 900 = 15 minutes)
  WAIT_INTERVAL_SECONDS — sleep between attempts (default 20)
"""
from __future__ import annotations

import os
import sys
import time

import requests


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return int(str(raw).strip())


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
    deadline = time.monotonic() + max_wait

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    # PostgREST root: cheap request that still touches the API gateway + project.
    probe = f"{url}/rest/v1/"

    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            r = requests.get(probe, headers=headers, timeout=45)
            # Paused / cold start often returns 5xx; 401 would mean bad key (fail fast).
            if r.status_code == 401:
                print(
                    "wait_for_supabase: HTTP 401 — check SUPABASE_SERVICE_ROLE_KEY",
                    file=sys.stderr,
                )
                return 1
            if r.status_code < 500:
                print(
                    f"wait_for_supabase: OK after {attempt} attempt(s) (HTTP {r.status_code})"
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


if __name__ == "__main__":
    sys.exit(main())
