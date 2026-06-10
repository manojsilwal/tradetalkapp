"""
Operator HTTP surface for the model-agnostic harness (Phases 1, 4, 5).

Endpoints
---------
* ``GET  /harness/status``          — kill-switch states + ledger counts
* ``POST /harness/replay``          — run a named candidate against graded history
* ``GET  /harness/replay/reports``  — persisted champion/challenger reports
* ``GET  /harness/hit-rates``       — hit rate by decision type + top features
* ``GET  /harness/calibration``     — forecast coverage / pinball by horizon
* ``POST /harness/model-backtest``  — walk-forward backtest of the forecaster
* ``POST /harness/self-learning/run`` — trigger the nightly Phase 3 pass now

Kill switch: ``HARNESS_API_ENABLE=0`` → every endpoint returns 503.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/harness", tags=["harness"])

_TRUTHY = ("1", "true", "yes", "on")


def _enabled() -> bool:
    return (os.getenv("HARNESS_API_ENABLE", "1").strip().lower() or "1") in _TRUTHY


def _guard() -> None:
    if not _enabled():
        raise HTTPException(status_code=503, detail="harness API disabled (HARNESS_API_ENABLE=0)")


# ── Models ───────────────────────────────────────────────────────────────────


class ReplayRequest(BaseModel):
    candidate: str = Field(
        default="stub",
        description="stub | stub:<verdict> | llm | llm:<role> | baseline_forecast | timesfm_service",
    )
    horizon: str = Field(default="5d")
    decision_type: Optional[str] = Field(default=None)
    since_days: float = Field(default=90.0, ge=1.0, le=730.0)
    limit: int = Field(default=100, ge=1, le=1000)
    min_labelled: int = Field(default=20, ge=1)
    min_delta: float = Field(default=0.0)


class ModelBacktestRequest(BaseModel):
    tickers: Optional[List[str]] = Field(default=None)
    horizon: str = Field(default="21d")
    threshold: float = Field(default=0.0)
    start: str = Field(default="2015-01-01")
    max_tickers: int = Field(default=20, ge=1, le=100)


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status")
def harness_status() -> Dict[str, Any]:
    _guard()
    from ..predictor.conformal import conformal_enabled, load_artifact
    from ..predictor.learned_weights import learned_weights_enabled, load_weights

    ledger_stats: Dict[str, Any] = {}
    try:
        from .. import decision_ledger as dl

        ledger_stats = dl.get_ledger().stats() or {}
    except Exception:
        ledger_stats = {}

    return {
        "harness_api_enable": _enabled(),
        "decision_ledger_enable": os.getenv("DECISION_LEDGER_ENABLE", "1"),
        "predictor_conformal_enable": conformal_enabled(),
        "predictor_learned_weights_enable": learned_weights_enabled(),
        "house_view_enable": os.getenv("HOUSE_VIEW_ENABLE", "1"),
        "timesfm_service_url_configured": bool((os.getenv("TIMESFM_SERVICE_URL") or "").strip()),
        "timesfm_remote_primary": os.getenv("TIMESFM_REMOTE_PRIMARY", "1"),
        "conformal_artifact": load_artifact() or None,
        "learned_weights_horizons": sorted(load_weights().keys()),
        "ledger_stats": ledger_stats,
        "generated_at": time.time(),
    }


@router.post("/replay")
async def run_replay_endpoint(req: ReplayRequest) -> Dict[str, Any]:
    _guard()
    from ..harness.replay_service import run_named_replay

    try:
        report, gate, row_id = await run_named_replay(
            req.candidate,
            horizon=req.horizon,
            decision_type=req.decision_type,
            since_days=req.since_days,
            limit=req.limit,
            min_labelled=req.min_labelled,
            min_delta=req.min_delta,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload = report.as_dict()
    payload["rows"] = payload["rows"][:20]  # keep API responses light
    return {
        "report": payload,
        "gate": {"passed": gate.passed, "reason": gate.reason},
        "stored_report_id": row_id,
    }


@router.get("/replay/reports")
def replay_reports(limit: int = Query(default=20, ge=1, le=200)) -> Dict[str, Any]:
    _guard()
    from ..harness.replay_service import list_reports

    return {"reports": list_reports(limit=limit)}


@router.get("/hit-rates")
def hit_rates(
    horizon: str = Query(default="5d"),
    min_n: int = Query(default=3, ge=1),
    limit: int = Query(default=15, ge=1, le=100),
) -> Dict[str, Any]:
    _guard()
    from .. import decision_ledger as dl
    from ..feature_correlations import compute_feature_stats

    by_type: List[Dict[str, Any]] = []
    try:
        conn = dl.get_ledger()._conn()  # type: ignore[attr-defined]
        if conn is not None:
            rows = conn.execute(
                """SELECT d.decision_type,
                          o.horizon,
                          COUNT(*) AS n,
                          AVG(CASE WHEN o.correct_bool = 1 THEN 1.0
                                   WHEN o.correct_bool = 0 THEN 0.0 END) AS hit_rate,
                          AVG(o.excess_return) AS mean_excess_return
                   FROM decision_events d
                   JOIN outcome_observations o
                     ON o.decision_id = d.decision_id AND o.metric = 'excess_return'
                   WHERE o.horizon = ?
                   GROUP BY d.decision_type, o.horizon
                   ORDER BY n DESC""",
                (horizon,),
            ).fetchall()
            by_type = [dict(r) for r in rows]
    except Exception:
        by_type = []

    feature_stats = [
        s.as_dict() for s in compute_feature_stats(horizon=horizon, min_n=min_n)
    ]
    feature_stats.sort(key=lambda s: (s["n_labelled"] or 0), reverse=True)

    return {
        "horizon": horizon,
        "hit_rate_by_decision_type": by_type,
        "feature_stats": feature_stats[:limit],
        "generated_at": time.time(),
    }


@router.get("/calibration")
def calibration(lookback_days: float = Query(default=90.0, ge=1.0)) -> Dict[str, Any]:
    _guard()
    from .. import decision_ledger as dl
    from ..predictor.conformal import TARGET_COVERAGE, load_artifact

    by_horizon: List[Dict[str, Any]] = []
    try:
        conn = dl.get_ledger()._conn()  # type: ignore[attr-defined]
        if conn is not None:
            cutoff = time.time() - lookback_days * 86400.0
            rows = conn.execute(
                """SELECT horizon,
                          SUM(CASE WHEN metric = 'forecast_band_hit' THEN 1 ELSE 0 END) AS n_graded,
                          AVG(CASE WHEN metric = 'forecast_band_hit' THEN value END) AS coverage,
                          AVG(CASE WHEN metric = 'forecast_pinball' THEN value END) AS mean_pinball,
                          AVG(CASE WHEN metric = 'forecast_point_err' THEN value END) AS mean_point_err
                   FROM outcome_observations
                   WHERE metric IN ('forecast_band_hit', 'forecast_pinball', 'forecast_point_err')
                     AND as_of_ts >= ?
                   GROUP BY horizon
                   ORDER BY horizon""",
                (cutoff,),
            ).fetchall()
            by_horizon = [dict(r) for r in rows]
    except Exception:
        by_horizon = []

    return {
        "target_coverage": TARGET_COVERAGE,
        "lookback_days": lookback_days,
        "by_horizon": by_horizon,
        "active_conformal_artifact": load_artifact() or None,
        "generated_at": time.time(),
    }


@router.post("/model-backtest")
def model_backtest(req: ModelBacktestRequest) -> Dict[str, Any]:
    _guard()
    from ..harness.model_backtest import run_model_backtest

    result = run_model_backtest(
        tickers=req.tickers,
        horizon=req.horizon,
        threshold=req.threshold,
        start=req.start,
        max_tickers=req.max_tickers,
    )
    return result.as_dict()


@router.post("/self-learning/run")
async def self_learning_run(dry_run: bool = Query(default=False)) -> Dict[str, Any]:
    """Manual trigger for the 02:40 UTC self-learning pass (operator tool)."""
    _guard()
    import asyncio

    from ..predictor.conformal import maybe_rollback, nightly_conformal_update
    from ..predictor.learned_weights import nightly_weights_update
    from ..sepl_market_fixtures import regenerate_fixtures

    rollback = await asyncio.to_thread(maybe_rollback)
    conformal = await asyncio.to_thread(nightly_conformal_update, dry_run=dry_run)
    weights = await asyncio.to_thread(nightly_weights_update, dry_run=dry_run)
    fixtures = await asyncio.to_thread(regenerate_fixtures) if not dry_run else {"skipped": "dry_run"}
    return {
        "rollback": rollback,
        "conformal": conformal,
        "learned_weights": weights,
        "market_fixtures": fixtures,
    }
