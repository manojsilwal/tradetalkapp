"""
Async batch scan orchestration for the Picks & Shovels Momentum Finder.

Clones the Actionable screener's worker shape (``run_actionable_scan`` /
``start_scan_task`` / job-state dict) but adds the **two-pass** flow the
cross-sectional score needs:

  pass 1  chunked fetch → raw per-ticker metrics for the whole universe
  pass 2  build the percentile context → score + classify + explain every row

Then persist one snapshot and emit the top picks to the Decision-Outcome Ledger.
No LLM in the hot path (deterministic explanations).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import data as ps_data
from . import explain as ps_explain
from . import ledger as ps_ledger
from . import scoring as ps_scoring
from . import store as ps_store
from . import themes as ps_themes

logger = logging.getLogger(__name__)


def _chunk_size() -> int:
    return max(1, int(os.environ.get("PICKS_SHOVELS_CHUNK_SIZE", "15") or "15"))


def _max_concurrency() -> int:
    return max(1, int(os.environ.get("PICKS_SHOVELS_MAX_CONCURRENCY", "10") or "10"))


def _inter_chunk_delay_s() -> float:
    return float(os.environ.get("PICKS_SHOVELS_INTER_CHUNK_DELAY_S", "0.5") or "0.5")


def _evidence_timeout_s() -> float:
    return float(os.environ.get("PICKS_SHOVELS_EVIDENCE_TIMEOUT_S", "8") or "8")


def get_universe() -> List[str]:
    return list(ps_themes.SEED_UNIVERSE)


# ── Job state (poll target for the frontend) ─────────────────────────────────

import threading

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


# ── Raw-row assembly (pass 1) ────────────────────────────────────────────────


def _build_raw_row(
    ticker: str,
    fund: Dict[str, Any],
    closes: List[float],
    *,
    operating: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    momo = ps_data.momentum_from_closes(closes)
    membership = ps_themes.membership_for(ticker)
    theme = {
        "themes": membership.themes if membership else [],
        "bottleneck_solved": membership.bottleneck_solved if membership else "",
        "hiddenness_seed": membership.hiddenness_seed if membership else "",
        "customer_capex_seed": membership.customer_capex_seed if membership else 60.0,
    }
    return {
        "ticker": ticker.upper(),
        "company_name": fund.get("company_name") or ticker.upper(),
        "sector": fund.get("sector"),
        "industry": fund.get("industry"),
        "momentum": momo,
        "fundamentals": fund,
        "operating": operating if operating is not None else {"available": False},
        "evidence": evidence if evidence is not None else {"available": False},
        "theme": theme,
    }


async def _safe_thread(fn, *args) -> Dict[str, Any]:
    """Run a blocking Phase-3 fetcher off-loop with a timeout; degrade on failure."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=_evidence_timeout_s())
    except Exception as e:
        logger.debug("[PicksShovels] %s failed: %s", getattr(fn, "__name__", fn), e)
        return {"available": False}


async def _process_chunk(chunk: List[str]) -> List[Dict[str, Any]]:
    closes_by_ticker: Dict[str, List[float]] = {}
    try:
        closes_by_ticker = await asyncio.to_thread(ps_data.fetch_price_series, chunk)
    except Exception as e:
        logger.warning("[PicksShovels] chunk history failed (%s…): %s", chunk[0] if chunk else "", e)

    sem = asyncio.Semaphore(_max_concurrency())

    async def _one(ticker: str) -> Optional[Dict[str, Any]]:
        async with sem:
            try:
                fund = await asyncio.to_thread(ps_data.fetch_fundamentals_extended, ticker)
            except Exception as e:
                logger.debug("[PicksShovels] fundamentals failed for %s: %s", ticker, e)
                fund = {"ticker": ticker.upper(), "company_name": ticker.upper()}
            company_name = fund.get("company_name") or ticker.upper()
            # Phase-3 network fetchers run off-loop under the same concurrency gate.
            evidence = await _safe_thread(ps_data.fetch_evidence, ticker, company_name)
            operating = await _safe_thread(ps_data.fetch_operating_metrics, ticker)
        return _build_raw_row(
            ticker,
            fund,
            closes_by_ticker.get(ticker, []),
            operating=operating,
            evidence=evidence,
        )

    results = await asyncio.gather(*(_one(t) for t in chunk))
    return [r for r in results if r is not None]


# ── Scoring + finalize (pass 2) ──────────────────────────────────────────────


def _finalize_rows(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ctx = ps_scoring.PercentileContext.build(raw_rows)
    out: List[Dict[str, Any]] = []
    for raw in raw_rows:
        scored = ps_scoring.score_row(raw, ctx)
        if scored.get("insufficient_data"):
            continue
        membership = ps_themes.membership_for(raw["ticker"])
        row: Dict[str, Any] = {
            "ticker": raw["ticker"],
            "company_name": raw["company_name"],
            "sector": raw.get("sector"),
            "industry": raw.get("industry"),
            "themes": (raw.get("theme") or {}).get("themes") or [],
            "theme_labels": [ps_themes.theme_label(t) for t in ((raw.get("theme") or {}).get("themes") or [])],
            "bottleneck_solved": membership.bottleneck_solved if membership else "",
            "final_score": scored["final_score"],
            "score_breakdown": scored["score_breakdown"],
            "coverage": scored["coverage"],
            "hiddenness_level": scored["hiddenness_level"],
            "hiddenness_score": scored["hiddenness_score"],
            "confidence_level": scored["confidence_level"],
            "confidence_score": scored["confidence_score"],
            "momentum": raw.get("momentum"),
            "fundamentals": raw.get("fundamentals"),
            "operating": raw.get("operating"),
            "evidence": raw.get("evidence"),
        }
        explanation = ps_explain.build_explanation(row)
        row["explanation"] = explanation
        row["why_selected"] = explanation["why_selected"]
        row["risks"] = explanation["risks"]
        out.append(row)
    out.sort(key=lambda r: r.get("final_score") or 0, reverse=True)
    return out


# ── Full scan ────────────────────────────────────────────────────────────────


async def run_scan(job_id: str, *, force: bool = False) -> Dict[str, Any]:
    if not force:
        cached = ps_store.fresh_snapshot_meta()
        if cached:
            _set_job(
                job_id=job_id,
                status="done",
                progress=100,
                message="Served from cached snapshot (fresh within the last hour).",
                snapshot_id=cached["snapshot_id"],
                cache_hit=True,
                processed=cached["scored"],
                total=cached["universe_size"],
                error=None,
            )
            return cached

    started = time.time()
    try:
        universe = get_universe()
        chunks = [universe[i : i + _chunk_size()] for i in range(0, len(universe), _chunk_size())]
        total = len(universe)
        _set_job(
            job_id=job_id,
            status="running",
            progress=1,
            message=f"Scanning {total} picks-and-shovels companies in {len(chunks)} chunks…",
            processed=0,
            total=total,
            snapshot_id=None,
            cache_hit=False,
            error=None,
        )

        raw_rows: List[Dict[str, Any]] = []
        processed = 0
        delay = _inter_chunk_delay_s()
        for idx, chunk in enumerate(chunks):
            chunk_rows = await _process_chunk(chunk)
            raw_rows.extend(chunk_rows)
            processed += len(chunk)
            _set_job(
                progress=max(1, min(90, int(processed / total * 88))),
                message=f"Fetched {processed}/{total} companies…",
                processed=processed,
            )
            if delay > 0 and idx < len(chunks) - 1:
                await asyncio.sleep(delay)

        _set_job(progress=92, message="Ranking cross-sectionally and scoring…")
        rows = await asyncio.to_thread(_finalize_rows, raw_rows)

        skipped = total - len(rows)
        snapshot_id = (
            f"ps_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        )
        _set_job(progress=95, message="Persisting snapshot…")
        await asyncio.to_thread(
            ps_store.persist_snapshot,
            snapshot_id,
            rows,
            universe_size=total,
            skipped=skipped,
            meta={
                "duration_s": round(time.time() - started, 1),
                "chunk_size": _chunk_size(),
                "force": force,
            },
        )

        _set_job(progress=97, message="Emitting decisions to ledger…")
        emitted = await asyncio.to_thread(ps_ledger.emit_decisions, rows, snapshot_id)

        _set_job(
            status="done",
            progress=100,
            message=(
                f"Scan complete: {len(rows)} scored, {skipped} skipped, "
                f"{emitted} ledger decisions."
            ),
            snapshot_id=snapshot_id,
            error=None,
        )
        return ps_store.latest_snapshot_meta() or {}
    except Exception as e:
        logger.exception("[PicksShovels] scan failed")
        _set_job(status="error", progress=100, message="Scan failed", error=str(e))
        raise


def start_scan_task(*, force: bool = False) -> Dict[str, Any]:
    global _worker_task
    job_id = uuid.uuid4().hex
    _set_job(
        job_id=job_id,
        status="running",
        progress=0,
        message="Queued picks-and-shovels scan…",
        processed=0,
        total=0,
        snapshot_id=None,
        cache_hit=False,
        error=None,
    )

    async def _runner() -> None:
        try:
            await run_scan(job_id, force=force)
        except Exception:
            pass

    _worker_task = asyncio.get_running_loop().create_task(_runner())
    return get_job_status()
