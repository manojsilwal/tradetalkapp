"""Pipeline Ops — admin-only, read-only view of the whole data pipeline.

Surfaces live GCP execution state (Cloud Run Jobs + Cloud Scheduler), BigQuery
freshness, the finance-brain snapshot freshness, the in-process knowledge
pipeline, and ledger/grader health on one page.

Every section is wrapped so a single unavailable dependency (e.g. no GCP
credentials in local dev) degrades to ``{"available": false, "reason": ...}``
instead of failing the whole probe — the page still renders locally.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from ..auth import get_current_admin_user

router = APIRouter(prefix="/pipeline-ops", tags=["pipeline-ops"])

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "tradetalkapp-492904")
REGION = os.environ.get("GCP_REGION", "us-central1")
CLOUD_RUN_JOBS = ["sp500-ingest", "sp500-daily-update", "brain-nightly", "fund-leaderboard-ingest"]
EXPECTED_SCHEDULER_JOBS = {
    "precompute-picks-shovels": "30 9 * * 1-5",
    "precompute-narrative-radar": "32 9 * * 1-5",
    "precompute-fund-leaderboard-metrics": "35 9 * * 1-5",
    "precompute-fund-leaderboard": "0 6 * * 1",
}
PAGE_SNAPSHOT_STALE_S = 26 * 3600  # alert when older than 26h on a trading day
BQ_FRESHNESS_TABLES = {
    "daily_prices": "trade_date",
    "daily_movement_features": "trade_date",
    "daily_brief_snapshot": "trade_date",
    "events_curated": "published_at",
}


def _cloud_run_jobs() -> Dict[str, Any]:
    try:
        from google.cloud import run_v2
        client = run_v2.ExecutionsClient()
        out: List[Dict[str, Any]] = []
        for job in CLOUD_RUN_JOBS:
            parent = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{job}"
            latest = None
            try:
                for ex in client.list_executions(parent=parent):
                    if latest is None or (ex.create_time and ex.create_time > latest.create_time):
                        latest = ex
            except Exception as e:  # noqa: BLE001 - per-job tolerance
                out.append({"job": job, "error": str(e)})
                continue
            if latest is None:
                out.append({"job": job, "state": "no_executions"})
                continue
            running = int(getattr(latest, "running_count", 0) or 0)
            succeeded = int(getattr(latest, "succeeded_count", 0) or 0)
            failed = int(getattr(latest, "failed_count", 0) or 0)
            state = "running" if running else ("succeeded" if succeeded and not failed else
                                               ("failed" if failed else "unknown"))
            out.append({
                "job": job,
                "state": state,
                "start_time": _iso(getattr(latest, "create_time", None)),
                "completion_time": _iso(getattr(latest, "completion_time", None)),
                "succeeded_count": succeeded,
                "failed_count": failed,
                "running_count": running,
            })
        return {"available": True, "jobs": out}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _scheduler() -> Dict[str, Any]:
    try:
        from google.cloud import scheduler_v1
        client = scheduler_v1.CloudSchedulerClient()
        parent = f"projects/{PROJECT_ID}/locations/{REGION}"
        out: List[Dict[str, Any]] = []
        for job in client.list_jobs(parent=parent):
            name = job.name.split("/")[-1]
            out.append({
                "name": name,
                "schedule": getattr(job, "schedule", ""),
                "time_zone": getattr(job, "time_zone", ""),
                "state": getattr(job.state, "name", str(job.state)),
                "last_attempt_time": _iso(getattr(job, "last_attempt_time", None)),
            })
        return {"available": True, "jobs": out}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _bigquery_freshness() -> Dict[str, Any]:
    try:
        from ..mcp_server.backend import backend
        bk = backend()
        tables: List[Dict[str, Any]] = []
        for table, date_col in BQ_FRESHNESS_TABLES.items():
            try:
                rows = bk.query(
                    f"SELECT COUNT(*) AS n, MAX({date_col}) AS latest FROM {table}"
                )
                row = rows[0] if rows else {}
                tables.append({"table": table, "rows": row.get("n"),
                               "latest": str(row.get("latest")) if row.get("latest") else None})
            except Exception as e:  # noqa: BLE001
                tables.append({"table": table, "error": str(e)})
        return {"available": True, "tables": tables}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _brain_freshness() -> Dict[str, Any]:
    try:
        from ..brain.run_brain_pipeline import read_status
        from ..brain.serving import serving_enabled
        status = read_status()
        return {
            "available": status is not None,
            "serving_enabled": serving_enabled(),
            "last_run": status,
            "reason": None if status else "no brain status.json yet (run the nightly brain pipeline)",
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _in_process_pipeline() -> Dict[str, Any]:
    try:
        from ..deps import knowledge_store
        stats = knowledge_store.stats()
        return {
            "available": True,
            "pipeline_status": stats.get("pipeline_status", {}),
            "collection_sizes": stats.get("collections", {}),
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _live_spot_activity(limit: int = 50) -> Dict[str, Any]:
    """Recent spot-price fetch events from the in-process ring buffer."""
    try:
        from ..connectors.spot import get_spot_activity, _spot_cache
        import time as _time
        now = _time.monotonic()
        cache_state = [
            {
                "ticker": sym,
                "price": q.price,
                "source": q.source,
                "degraded": q.degraded,
                "ttl_remaining_s": round(max(0.0, expires - now), 1),
            }
            for sym, (q, expires) in sorted(_spot_cache.items())
            if expires > now
        ]
        return {
            "available": True,
            "recent_fetches": get_spot_activity(limit),
            "cache_size": len(cache_state),
            "cache_entries": cache_state,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _ledger_health() -> Dict[str, Any]:
    try:
        from .. import decision_ledger as dl
        return {"available": True, "stats": dl.get_ledger().stats()}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _page_snapshots() -> Dict[str, Any]:
    """Freshness of the three precomputed global intelligence pages."""
    import time
    from datetime import datetime, timezone

    try:
        from .. import durable_snapshot
        from .. import fund_leaderboard_store as fl_store
        from ..narrative_radar import store as nr_store
        from ..picks_shovels import store as ps_store

        now = time.time()
        pages: List[Dict[str, Any]] = []

        def _age_row(page: str, created_at: Optional[float], **extra: Any) -> Dict[str, Any]:
            if created_at is None:
                return {
                    "page": page,
                    "snapshot_id": None,
                    "age_seconds": None,
                    "is_fresh": False,
                    "stale_alert": True,
                    **extra,
                }
            age = max(0, int(now - created_at))
            ttl = extra.pop("ttl_s", 86400)
            return {
                "page": page,
                "age_seconds": age,
                "is_fresh": age <= ttl,
                "stale_alert": age > PAGE_SNAPSHOT_STALE_S,
                **extra,
            }

        ps_meta = ps_store.latest_snapshot_meta()
        if ps_meta:
            pages.append(_age_row(
                "picks_shovels",
                ps_meta.get("created_at"),
                snapshot_id=ps_meta.get("snapshot_id"),
                scored=ps_meta.get("scored"),
                ttl_s=ps_store.cache_ttl_s(),
            ))
        else:
            pages.append(_age_row("picks_shovels", None))

        nr_meta = nr_store.latest_snapshot_meta()
        if nr_meta:
            pages.append(_age_row(
                "narrative_radar",
                nr_meta.get("created_at"),
                snapshot_id=nr_meta.get("snapshot_id"),
                scored=nr_meta.get("scored"),
                ttl_s=nr_store.cache_ttl_s(),
            ))
        else:
            pages.append(_age_row("narrative_radar", None))

        fl_lb = fl_store.get_leaderboard(limit=1)
        fl_rows = fl_lb.get("rows") or []
        as_of = fl_lb.get("asOfDate")
        fl_created = None
        if as_of:
            try:
                fl_created = datetime.fromisoformat(str(as_of)).replace(tzinfo=timezone.utc).timestamp()
            except Exception:  # noqa: BLE001
                fl_created = None
        pages.append(_age_row(
            "fund_leaderboard",
            fl_created,
            row_count=len(fl_rows),
            as_of_date=as_of,
            ttl_s=86400,
        ))

        return {
            "available": True,
            "durable_snapshot_active": durable_snapshot.active(),
            "schedule_note": "Weekday precompute at 9:30 AM ET (America/New_York)",
            "pages": pages,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def _iso(ts) -> Any:
    if ts is None:
        return None
    try:
        return ts.isoformat()
    except Exception:  # noqa: BLE001
        return str(ts)


@router.get("/status", dependencies=[Depends(get_current_admin_user)])
def pipeline_ops_status() -> Dict[str, Any]:
    return {
        "project_id": PROJECT_ID,
        "region": REGION,
        "cloud_run_jobs": _cloud_run_jobs(),
        "cloud_scheduler": _scheduler(),
        "bigquery_freshness": _bigquery_freshness(),
        "brain": _brain_freshness(),
        "live_spot_activity": _live_spot_activity(),
        "in_process_pipeline": _in_process_pipeline(),
        "ledger": _ledger_health(),
        "page_snapshots": _page_snapshots(),
        "expected_scheduler_jobs": EXPECTED_SCHEDULER_JOBS,
    }


@router.get("/spot-activity", dependencies=[Depends(get_current_admin_user)])
def spot_activity_only() -> Dict[str, Any]:
    """Lightweight endpoint — poll this frequently without loading the whole status."""
    return _live_spot_activity(limit=100)
