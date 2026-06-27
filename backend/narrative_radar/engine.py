"""
Async batch scan orchestration for the Narrative Rotation Radar (Plan NR-3/NR-4).

Two-pass flow (cloned from ``backend/picks_shovels/engine.py``):
  pass 1  chunked fetch → member close series + market caps for the whole universe;
          build per-theme raw features (market confirmation + breadth)
  pass 2  build the cross-sectional ThemeContext → score + classify phase + explain
          every theme; persist one snapshot; emit theme-phase verdicts to the ledger.

The pass-2 logic is exposed as the pure, offline-testable ``assemble_theme_rows``.
No LLM in the hot path (deterministic explanations).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import data as nr_data
from . import explain as nr_explain
from . import features as nr_features
from . import ledger as nr_ledger
from . import lifecycle as nr_lifecycle
from . import scoring as nr_scoring
from . import store as nr_store
from . import themes as nr_themes

logger = logging.getLogger(__name__)


def _chunk_size() -> int:
    return max(1, int(os.environ.get("NARRATIVE_RADAR_CHUNK_SIZE", "8") or "8"))


def _inter_chunk_delay_s() -> float:
    return float(os.environ.get("NARRATIVE_RADAR_INTER_CHUNK_DELAY_S", "1.0") or "1.0")


def _chunk_history_timeout_s() -> float:
    return float(os.environ.get("NARRATIVE_RADAR_CHUNK_HISTORY_TIMEOUT_S", "90") or "90")


def _fundamentals_timeout_s() -> float:
    return float(os.environ.get("NARRATIVE_RADAR_FUNDAMENTALS_TIMEOUT_S", "12") or "12")


def _executor_workers() -> int:
    return max(2, int(os.environ.get("NARRATIVE_RADAR_EXECUTOR_WORKERS", "8") or "8"))


_EXECUTOR = ThreadPoolExecutor(max_workers=_executor_workers(), thread_name_prefix="nr-scan")


async def _in_executor(fn, *args) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, fn, *args)


def _reset_executor() -> None:
    global _EXECUTOR
    try:
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    _EXECUTOR = ThreadPoolExecutor(max_workers=_executor_workers(), thread_name_prefix="nr-scan")


def get_universe() -> List[str]:
    return list(nr_themes.theme_universe())


# ── Job state (poll target for the frontend) ─────────────────────────────────

_job_lock = threading.Lock()
_job: Dict[str, Any] = {
    "job_id": None,
    "status": "idle",  # idle | running | done | error
    "progress": 0,
    "message": "",
    "processed": 0,
    "total": 0,
    "snapshot_id": None,
    "cache_hit": False,
    "error": None,
    "updated_at": None,
}
_worker_task: Optional[asyncio.Task] = None


def get_job_status() -> Dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set_job(**kwargs: Any) -> None:
    with _job_lock:
        _job.update(kwargs)
        _job["updated_at"] = datetime.now(timezone.utc).isoformat()


# ── Pass 2 — pure scoring/classification/explanation (offline-testable) ───────


def assemble_theme_rows(feature_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Given raw per-theme feature dicts (from ``features.build_theme_features``),
    build the cross-sectional context, score + classify + explain every theme.
    Pure function — no I/O — so tests can feed synthetic features.
    """
    ctx = nr_scoring.ThemeContext.build(feature_rows)
    out: List[Dict[str, Any]] = []
    for feat in feature_rows:
        theme_id = feat["theme_id"]
        scored = nr_scoring.score_theme(feat, ctx)
        if scored.get("insufficient_data"):
            continue
        phase = nr_lifecycle.classify_theme_phase(
            scored["scores"], scored.get("confidence_score") or 0.0
        )
        explanation = nr_explain.build_explanation(theme_id, feat, scored, phase)
        row: Dict[str, Any] = {
            "theme_id": theme_id,
            "theme_label": nr_themes.theme_label(theme_id),
            "bottleneck": nr_themes.theme_bottleneck(theme_id),
            "lifecycle_phase": phase,
            "phase_label": nr_lifecycle.phase_label(phase),
            "recommendation_label": nr_lifecycle.recommendation_label(phase),
            "scores": scored["scores"],
            "coverage": scored["coverage"],
            "confidence_score": scored["confidence_score"],
            "confidence_level": scored["confidence_level"],
            "available_families": scored["available_families"],
            "unavailable_families": scored["unavailable_families"],
            "features": feat,
            "explanation": explanation,
            "summary": explanation["summary"],
        }
        out.append(row)
    # Rank: most-at-risk (exit) first is useful, but default to acceleration desc for discovery.
    out.sort(key=lambda r: (r["scores"].get("theme_acceleration_score") or 0), reverse=True)
    return out


# ── Pass 1 — live fetch + feature build ───────────────────────────────────────


async def _fetch_universe(universe: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch close series + market cap for every universe ticker. Degrades per-ticker."""
    chunks = [universe[i : i + _chunk_size()] for i in range(0, len(universe), _chunk_size())]
    closes_by_ticker: Dict[str, List[float]] = {}
    funds_by_ticker: Dict[str, Dict[str, Any]] = {}
    delay = _inter_chunk_delay_s()
    total = len(universe)
    processed = 0

    for idx, chunk in enumerate(chunks):
        try:
            chunk_closes = await asyncio.wait_for(
                _in_executor(nr_data.fetch_closes, chunk),
                timeout=_chunk_history_timeout_s(),
            )
            closes_by_ticker.update(chunk_closes or {})
        except Exception as e:
            logger.warning("[NarrativeRadar] chunk history failed (%s…): %s", chunk[0] if chunk else "", e)

        async def _fund(tk: str) -> None:
            try:
                f = await asyncio.wait_for(
                    _in_executor(nr_data.fetch_market_cap, tk),
                    timeout=_fundamentals_timeout_s(),
                )
                funds_by_ticker[tk] = f or {}
            except Exception:
                funds_by_ticker[tk] = {}

        await asyncio.gather(*(_fund(tk) for tk in chunk))

        processed += len(chunk)
        _set_job(progress=max(1, min(80, int(processed / total * 78))),
                 message=f"Fetched {processed}/{total} theme members…", processed=processed)
        if idx < len(chunks) - 1:
            _reset_executor()
            if delay > 0:
                await asyncio.sleep(delay)

    out: Dict[str, Dict[str, Any]] = {}
    for tk in universe:
        out[tk] = nr_data.build_member_row(tk, closes_by_ticker.get(tk, []), funds_by_ticker.get(tk, {}))
    return out


def _build_feature_rows(member_by_ticker: Dict[str, Dict[str, Any]], spy_closes: List[float]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for tid in nr_themes.theme_ids():
        members = [member_by_ticker[tk] for tk in nr_themes.theme_members(tid) if tk in member_by_ticker]
        if not members:
            continue
        rows.append(nr_features.build_theme_features(tid, members, spy_closes))
    return rows


async def run_scan(job_id: str, *, force: bool = False) -> Dict[str, Any]:
    if not force:
        cached = nr_store.fresh_snapshot_meta()
        if cached:
            _set_job(job_id=job_id, status="done", progress=100,
                     message="Served from cached snapshot (fresh within the last hour).",
                     snapshot_id=cached["snapshot_id"], cache_hit=True,
                     processed=cached["scored"], total=cached["theme_count"], error=None)
            return cached

    started = time.time()
    try:
        universe = get_universe()
        _set_job(job_id=job_id, status="running", progress=1,
                 message=f"Scanning {len(nr_themes.theme_ids())} themes ({len(universe)} members)…",
                 processed=0, total=len(universe), snapshot_id=None, cache_hit=False, error=None)

        member_by_ticker = await _fetch_universe(universe)
        _set_job(progress=82, message="Fetching benchmark (SPY)…")
        spy_closes = await asyncio.wait_for(
            _in_executor(nr_data.fetch_benchmark_closes), timeout=_chunk_history_timeout_s()
        )

        _set_job(progress=88, message="Building theme features…")
        feature_rows = await asyncio.to_thread(_build_feature_rows, member_by_ticker, spy_closes)

        _set_job(progress=92, message="Scoring + classifying lifecycle phases…")
        rows = await asyncio.to_thread(assemble_theme_rows, feature_rows)

        skipped = len(nr_themes.theme_ids()) - len(rows)
        snapshot_id = f"nr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        _set_job(progress=95, message="Persisting snapshot…")
        await asyncio.to_thread(
            nr_store.persist_snapshot, snapshot_id, rows,
            theme_count=len(nr_themes.theme_ids()), skipped=skipped,
            meta={"duration_s": round(time.time() - started, 1), "force": force},
        )

        _set_job(progress=97, message="Emitting theme-phase decisions to ledger…")
        emitted = await asyncio.to_thread(nr_ledger.emit_decisions, rows, snapshot_id)

        _set_job(status="done", progress=100,
                 message=f"Scan complete: {len(rows)} themes scored, {emitted} ledger decisions.",
                 snapshot_id=snapshot_id, error=None)
        return nr_store.latest_snapshot_meta() or {}
    except Exception as e:
        logger.exception("[NarrativeRadar] scan failed")
        _set_job(status="error", progress=100, message="Scan failed", error=str(e))
        raise


def start_scan_task(*, force: bool = False) -> Dict[str, Any]:
    global _worker_task
    job_id = uuid.uuid4().hex
    _set_job(job_id=job_id, status="running", progress=0, message="Queued narrative radar scan…",
             processed=0, total=0, snapshot_id=None, cache_hit=False, error=None)

    async def _runner() -> None:
        try:
            await run_scan(job_id, force=force)
        except Exception:
            pass

    _worker_task = asyncio.get_running_loop().create_task(_runner())
    return get_job_status()
