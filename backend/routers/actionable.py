"""
Actionable Companies API — async S&P 500 batch screener.

POST /actionable-companies/run      → 202 Accepted, job handed to async worker
GET  /actionable-companies/status   → poll target for the frontend progress UI
GET  /actionable-companies/results  → top candidates from the latest snapshot
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from .. import actionable_companies as svc
from ..rate_limiter import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/actionable-companies", tags=["actionable-companies"])

_rl = rate_limit("default")
_rl_expensive = rate_limit("expensive")


@router.post("/run", dependencies=[Depends(_rl_expensive)])
async def run_actionable_scan(
    force: bool = Query(
        False,
        description="If true, bypass the 1-hour snapshot cache and rescan now",
    ),
) -> JSONResponse:
    """
    Trigger the S&P 500 actionable scan. Lightweight by design:

    - a scan is already running → 200 with ``accepted: false``
    - a snapshot fresher than 1 hour exists (and not ``force``) → 200 with
      ``cache_hit: true`` (frontend reads results immediately, no recompute)
    - otherwise → **202 Accepted** and the job runs on an async worker
    """
    status = svc.get_job_status()
    if status.get("status") == "running":
        return JSONResponse(
            status_code=200,
            content={"accepted": False, "reason": "already_running", "job": status},
        )

    if not force:
        cached = svc.fresh_snapshot_meta()
        if cached:
            return JSONResponse(
                status_code=200,
                content={
                    "accepted": False,
                    "cache_hit": True,
                    "reason": "fresh_snapshot",
                    "snapshot": cached,
                },
            )

    job = svc.start_scan_task(force=force)
    return JSONResponse(
        status_code=202,
        content={"accepted": True, "cache_hit": False, "job": job},
    )


@router.get("/status", dependencies=[Depends(_rl)])
async def get_scan_status() -> Dict[str, Any]:
    return svc.get_job_status()


@router.get("/results", dependencies=[Depends(_rl)])
async def get_scan_results(
    limit: int = Query(25, ge=1, le=100),
    actionable_only: bool = Query(True),
) -> Dict[str, Any]:
    meta = svc.latest_snapshot_meta()
    if not meta:
        return {
            "snapshot": None,
            "rows": [],
            "message": "No actionable-companies snapshot yet. Trigger a scan first.",
        }
    rows = svc.load_snapshot_rows(
        meta["snapshot_id"], limit=limit, actionable_only=actionable_only
    )
    age_s = max(0, int(time.time() - meta["created_at"]))
    return {
        "snapshot": meta,
        "age_seconds": age_s,
        "is_fresh": age_s <= svc._cache_ttl_s(),
        "rows": rows,
    }
