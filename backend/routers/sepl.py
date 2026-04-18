"""
HTTP surface for the SEPL (Self-Evolution Protocol Layer, Phase B).

Endpoints:
  GET  /sepl/status             — feature flag + tunables (always safe)
  GET  /sepl/select/preview     — what Select would pick right now (read-only)
  POST /sepl/run                — run a full cycle; body ``{"dry_run": bool,
                                  "target": "prompt_name"?}``

Safety:
  * All endpoints refuse unless ``SEPL_ENABLE=1``. This is a deliberate
    second gate on top of the ``sepl_dry_run`` default — ``/sepl/run`` cannot
    mutate anything unless BOTH ``SEPL_ENABLE=1`` AND ``dry_run=false`` AND
    the caller explicitly passes ``commit: true``.
  * ``POST /sepl/run`` returns a full :class:`~backend.sepl.CycleReport` as JSON
    so operators can inspect what the cycle did (or would have done).
  * No write endpoints for registry state live here — those go through the
    core registry via the SEPL operator or human review.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..deps import knowledge_store, llm_client, resource_registry
from ..sepl import (
    SEPL,
    KnowledgeStoreReflectionSource,
    RollbackOutcome,
    SEPLKillSwitch,
    SEPLOutcome,
    sepl_dry_run,
    sepl_effectiveness_ceiling,
    sepl_enabled,
    sepl_max_commits_per_day,
    sepl_min_margin,
    sepl_min_samples,
    sepl_rollback_margin,
    sepl_rollback_min_samples,
    sepl_rollback_window_hours,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sepl", tags=["sepl"])


# ── Response models ──────────────────────────────────────────────────────────


class SEPLStatus(BaseModel):
    enabled: bool
    dry_run_default: bool
    tunables: Dict[str, float]
    rollback_tunables: Dict[str, float]
    fixtures_dir: str


class SelectPreview(BaseModel):
    target: Optional[str]
    reason: str
    candidates: List[Dict[str, Any]]


class RunRequest(BaseModel):
    """Body for ``POST /sepl/run``."""

    dry_run: Optional[bool] = Field(
        default=None,
        description=(
            "Override the default dry-run behavior. None → fall back to "
            "SEPL_DRY_RUN env (default 1)."
        ),
    )
    target: Optional[str] = Field(
        default=None,
        description="Bypass Select and run the cycle on this prompt name.",
    )
    commit: bool = Field(
        default=False,
        description=(
            "Explicit permission to mutate the registry on this call. Even "
            "when dry_run=false, the endpoint refuses to commit unless this "
            "flag is true. Belt-and-suspenders for Phase B."
        ),
    )


class RunResponse(BaseModel):
    run_id: str
    outcome: str
    committed_version: Optional[str] = None
    dry_run: bool
    target: Optional[str] = None
    margin: Optional[float] = None
    active_score: Optional[float] = None
    candidate_score: Optional[float] = None
    fixtures_used: Optional[int] = None
    sample_size: Optional[int] = None
    rationale: Optional[str] = None
    elapsed_sec: float


class RollbackRequest(BaseModel):
    """Body for ``POST /sepl/kill-switch/run``."""

    target: Optional[str] = Field(
        default=None,
        description="Inspect only this prompt. Null → check every learnable prompt.",
    )
    dry_run: bool = Field(
        default=True,
        description=(
            "When True (default), compute the decision but do NOT call restore. "
            "Set False AND include ``commit: true`` to actually roll back."
        ),
    )
    commit: bool = Field(
        default=False,
        description=(
            "Belt-and-suspenders: must be true AND dry_run must be false "
            "for the endpoint to perform an actual restore."
        ),
    )


class RollbackReportResponse(BaseModel):
    run_id: str
    target_name: str
    outcome: str
    committed_version: Optional[str] = None
    prior_version: Optional[str] = None
    post_commit_effectiveness: Optional[float] = None
    pre_commit_effectiveness: Optional[float] = None
    delta: Optional[float] = None
    post_commit_samples: int
    pre_commit_samples: int
    restored_to_version: Optional[str] = None
    dry_run: bool


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sepl() -> SEPL:
    return SEPL(
        llm_client=llm_client,
        registry=resource_registry,
        reflection_source=KnowledgeStoreReflectionSource(knowledge_store),
    )


def _kill_switch() -> SEPLKillSwitch:
    return SEPLKillSwitch(
        registry=resource_registry,
        reflection_source=KnowledgeStoreReflectionSource(knowledge_store),
    )


def _guard_enabled() -> None:
    if not sepl_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "SEPL is disabled. Set SEPL_ENABLE=1 in the environment and "
                "restart to enable the evolution loop."
            ),
        )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status", response_model=SEPLStatus)
def get_status() -> SEPLStatus:
    """Tunables + flag state. Always safe to call."""
    from ..sepl import DEFAULT_FIXTURES_DIR

    return SEPLStatus(
        enabled=sepl_enabled(),
        dry_run_default=sepl_dry_run(),
        tunables={
            "min_samples": float(sepl_min_samples()),
            "min_margin": sepl_min_margin(),
            "max_commits_per_day": float(sepl_max_commits_per_day()),
            "effectiveness_ceiling": sepl_effectiveness_ceiling(),
        },
        rollback_tunables={
            "margin": sepl_rollback_margin(),
            "min_samples": float(sepl_rollback_min_samples()),
            "window_hours": float(sepl_rollback_window_hours()),
        },
        fixtures_dir=str(DEFAULT_FIXTURES_DIR),
    )


@router.get("/select/preview", response_model=SelectPreview)
def preview_select() -> SelectPreview:
    """What Select would pick right now. Read-only; does not mutate."""
    _guard_enabled()
    decision = _sepl().select()
    return SelectPreview(
        target=decision.target_name,
        reason=decision.reason,
        candidates=[
            {"prompt_name": name, "effectiveness_mean": round(score, 4)}
            for name, score in decision.candidates_considered
        ],
    )


@router.post("/run", response_model=RunResponse)
async def run_cycle(req: RunRequest) -> RunResponse:
    """
    Run a single Reflect→Select→Improve→Evaluate→Commit cycle.

    The cycle is a no-op unless ``SEPL_ENABLE=1``. Commits additionally require
    both ``dry_run=false`` AND ``commit=true`` in the request body.
    """
    _guard_enabled()

    # Decide the effective dry-run flag. Request overrides env; if neither
    # says "live", we stay dry.
    if req.dry_run is not None:
        effective_dry = bool(req.dry_run)
    else:
        effective_dry = sepl_dry_run()

    # Belt-and-suspenders: if caller didn't set commit=true, force dry-run.
    if not req.commit:
        effective_dry = True

    report = await _sepl().run_cycle(dry_run=effective_dry, force_target=req.target)

    evaluation = report.evaluation
    proposal = report.proposal
    reflect = report.reflect

    return RunResponse(
        run_id=report.run_id,
        outcome=report.outcome.value,
        committed_version=report.committed_version,
        dry_run=report.dry_run,
        target=report.select.target_name if report.select else None,
        margin=evaluation.margin if evaluation else None,
        active_score=evaluation.active_score if evaluation else None,
        candidate_score=evaluation.candidate_score if evaluation else None,
        fixtures_used=evaluation.fixtures_used if evaluation else None,
        sample_size=reflect.sample_size if reflect else None,
        rationale=proposal.rationale if proposal else None,
        elapsed_sec=report.elapsed_sec,
    )


# ── Kill-switch endpoints (PR 6) ─────────────────────────────────────────────


def _report_to_response(report) -> RollbackReportResponse:
    return RollbackReportResponse(
        run_id=report.run_id,
        target_name=report.target_name,
        outcome=report.outcome.value,
        committed_version=report.committed_version,
        prior_version=report.prior_version,
        post_commit_effectiveness=report.post_commit_effectiveness,
        pre_commit_effectiveness=report.pre_commit_effectiveness,
        delta=report.delta,
        post_commit_samples=report.post_commit_samples,
        pre_commit_samples=report.pre_commit_samples,
        restored_to_version=report.restored_to_version,
        dry_run=report.dry_run,
    )


@router.post("/kill-switch/run", response_model=List[RollbackReportResponse])
def kill_switch_run(req: RollbackRequest):
    """
    Run the auto-rollback kill switch.

    * ``target`` omitted → inspect every learnable prompt.
    * ``dry_run=true`` (default) → compute the decision but never restore.
    * ``dry_run=false`` AND ``commit=true`` → actually restore on regression.

    The combination of two flags is intentional: the kill switch is more
    destructive than a SEPL commit (it reverts a change that was already in
    production) so we want an explicit "yes I mean it" from the caller.
    """
    _guard_enabled()

    effective_dry = bool(req.dry_run)
    if not req.commit:
        effective_dry = True

    ks = _kill_switch()
    if req.target:
        reports = [ks.check(req.target, dry_run=effective_dry)]
    else:
        reports = ks.check_all(dry_run=effective_dry)
    return [_report_to_response(r) for r in reports]


@router.get("/kill-switch/preview", response_model=List[RollbackReportResponse])
def kill_switch_preview():
    """Dry-run-only preview across every learnable prompt. Read-only."""
    _guard_enabled()
    reports = _kill_switch().check_all(dry_run=True)
    return [_report_to_response(r) for r in reports]
