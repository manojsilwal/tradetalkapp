"""
Model-swap replay harness (Harness Engineering Phase 2).

Before we promote a new candidate model — GPT-5.2, Gemini 2.5, whatever
ships next — we want to answer **"would this model have made better
decisions on our own history?"**. That's the whole point of the
Decision-Outcome Ledger: the inputs (prompt_versions, evidence, features)
and the graded outcomes are already stored, so we can replay a candidate
against past decisions and compare its verdicts against ours without
touching the production service.

This module is intentionally a thin, pure-Python orchestrator:

* It does NOT call any specific LLM API directly. Callers inject a
  ``CandidateRunner`` async callable that takes one :class:`DecisionEvent`
  (plus its evidence + features) and returns a :class:`CandidateVerdict`.
  That keeps tests offline and keeps the replay harness independent of any
  particular provider integration.
* It reads the incumbent decisions + outcomes straight from the ledger
  (``list_decisions_since`` + ``get_outcomes_for_decision``).
* It emits a structured comparison report — not a new ledger row — because
  candidate runs should be reproducible, not archived as if they were
  real production decisions. The report is what the operator reviews
  before flipping the ``GEMINI_PRIMARY`` / ``OPENROUTER_MODEL`` env vars.

See the ``replay_harness`` todo in the original plan for the intent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from . import decision_ledger as _dl

logger = logging.getLogger(__name__)


# ── Data contracts ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateVerdict:
    """What the candidate model produced for one historical decision."""

    decision_id: str
    verdict: str
    confidence: Optional[float] = None
    model: str = ""
    output: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class ReplayComparison:
    """Per-decision comparison row used to build the summary report."""

    decision_id: str
    symbol: str
    horizon_hint: str
    incumbent_verdict: str
    incumbent_confidence: Optional[float]
    incumbent_excess_return: Optional[float]
    incumbent_correct: Optional[bool]
    candidate_verdict: str
    candidate_confidence: Optional[float]
    candidate_correct: Optional[bool]
    agree: Optional[bool]
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "horizon_hint": self.horizon_hint,
            "incumbent_verdict": self.incumbent_verdict,
            "incumbent_confidence": self.incumbent_confidence,
            "incumbent_excess_return": self.incumbent_excess_return,
            "incumbent_correct": self.incumbent_correct,
            "candidate_verdict": self.candidate_verdict,
            "candidate_confidence": self.candidate_confidence,
            "candidate_correct": self.candidate_correct,
            "agree": self.agree,
            "error": self.error,
        }


@dataclass
class ReplayReport:
    """Aggregate summary returned by :func:`run_replay`."""

    horizon: str
    candidate_model: str
    n_considered: int = 0
    n_replayed: int = 0
    n_errors: int = 0
    n_agree: int = 0
    n_both_labelled: int = 0
    incumbent_hit_rate: Optional[float] = None
    candidate_hit_rate: Optional[float] = None
    delta_hit_rate: Optional[float] = None
    incumbent_mean_excess_return: Optional[float] = None
    rows: List[ReplayComparison] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "horizon": self.horizon,
            "candidate_model": self.candidate_model,
            "n_considered": self.n_considered,
            "n_replayed": self.n_replayed,
            "n_errors": self.n_errors,
            "n_agree": self.n_agree,
            "n_both_labelled": self.n_both_labelled,
            "incumbent_hit_rate": self.incumbent_hit_rate,
            "candidate_hit_rate": self.candidate_hit_rate,
            "delta_hit_rate": self.delta_hit_rate,
            "incumbent_mean_excess_return": self.incumbent_mean_excess_return,
            "rows": [r.as_dict() for r in self.rows],
        }


CandidateRunner = Callable[
    [_dl.DecisionEvent, List[Dict[str, Any]], List[Dict[str, Any]]],
    Awaitable[CandidateVerdict],
]


# ── Correctness helper (mirror outcome_grader) ─────────────────────────────

_BUY_VERDICTS = {"BUY", "STRONG BUY"}
_SELL_VERDICTS = {"SELL", "STRONG SELL"}


def _verdict_is_directional(v: str) -> bool:
    u = (v or "").upper().strip()
    return u in _BUY_VERDICTS or u in _SELL_VERDICTS


def _candidate_correctness(verdict: str, excess_return: Optional[float]) -> Optional[bool]:
    """Same rules as :mod:`backend.outcome_grader._grade_correctness` — kept
    local to avoid the import cycle.
    """
    if excess_return is None:
        return None
    u = (verdict or "").upper().strip()
    if u in _BUY_VERDICTS:
        return excess_return > 0
    if u in _SELL_VERDICTS:
        return excess_return < 0
    return None


def _fetch_evidence(ledger: Any, decision_id: str) -> List[Dict[str, Any]]:
    try:
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception:
        return []
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT chunk_id, collection, relevance, rank FROM decision_evidence "
            "WHERE decision_id = ? ORDER BY rank ASC",
            (decision_id,),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "chunk_id": r["chunk_id"],
            "collection": r["collection"],
            "relevance": r["relevance"],
            "rank": r["rank"],
        }
        for r in rows
    ]


def _fetch_features(ledger: Any, decision_id: str) -> List[Dict[str, Any]]:
    try:
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception:
        return []
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT feature_name, value_num, value_str, regime FROM feature_snapshots "
            "WHERE decision_id = ?",
            (decision_id,),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "feature_name": r["feature_name"],
            "value_num": r["value_num"],
            "value_str": r["value_str"],
            "regime": r["regime"],
        }
        for r in rows
    ]


def _fetch_outcome(
    ledger: Any, decision_id: str, horizon: str
) -> Optional[Dict[str, Any]]:
    try:
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception:
        return None
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT metric, value, benchmark, excess_return, correct_bool "
            "FROM outcome_observations "
            "WHERE decision_id = ? AND horizon = ? AND metric = 'excess_return'",
            (decision_id, horizon),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return {
        "value": row["value"],
        "benchmark": row["benchmark"],
        "excess_return": row["excess_return"],
        "correct_bool": row["correct_bool"],
    }


async def run_replay(
    runner: CandidateRunner,
    *,
    horizon: str = "5d",
    decision_type: Optional[str] = None,
    since_ts: float = 0.0,
    limit: int = 100,
    ledger: Any = None,
    candidate_model: str = "candidate",
    max_concurrency: int = 4,
) -> ReplayReport:
    """Replay past decisions through ``runner`` and compare against ledger truth.

    Parameters
    ----------
    runner: async ``(event, evidence, features) -> CandidateVerdict``. Injected
        so tests can run entirely offline and so callers pick the wiring
        (LLMClient, direct OpenRouter, Gemini, local model, etc.).
    horizon: ledger horizon to use when scoring the candidate's correctness.
        Must be one of ``"1d" | "5d" | "21d" | "63d"``.
    decision_type: optional filter — e.g. replay only debates.
    since_ts: only replay decisions created on/after this epoch-ts.
    limit: hard cap on the number of decisions to process.
    candidate_model: tag used in the report; also defaulted into
        :class:`CandidateVerdict` if the runner doesn't fill it in.
    max_concurrency: async semaphore width — keeps the candidate provider
        from being overwhelmed when replaying thousands of decisions.
    """
    ledger = ledger or _dl.get_ledger()
    events = ledger.list_decisions_since(
        since_ts, decision_type=decision_type, limit=int(limit),
    )
    report = ReplayReport(
        horizon=horizon,
        candidate_model=candidate_model,
        n_considered=len(events),
    )
    if not events:
        return report

    sem = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def _one(ev: _dl.DecisionEvent) -> ReplayComparison:
        evidence = _fetch_evidence(ledger, ev.decision_id)
        features = _fetch_features(ledger, ev.decision_id)
        outcome = _fetch_outcome(ledger, ev.decision_id, horizon)
        incumbent_excess = None
        incumbent_correct: Optional[bool] = None
        if outcome is not None:
            try:
                incumbent_excess = (
                    float(outcome["excess_return"])
                    if outcome["excess_return"] is not None
                    else None
                )
            except Exception:
                incumbent_excess = None
            cb = outcome.get("correct_bool")
            if cb is not None:
                try:
                    incumbent_correct = bool(int(cb))
                except Exception:
                    incumbent_correct = None

        err = ""
        cand = CandidateVerdict(
            decision_id=ev.decision_id,
            verdict="",
            model=candidate_model,
        )
        async with sem:
            try:
                cand = await runner(ev, evidence, features)
            except Exception as e:
                err = f"runner_error: {e}"[:300]
                logger.warning(
                    "[ModelSwapReplay] runner failed decision=%s: %s",
                    ev.decision_id, e,
                )

        candidate_correct = _candidate_correctness(cand.verdict, incumbent_excess)

        agree: Optional[bool]
        if not cand.verdict or not ev.verdict:
            agree = None
        else:
            agree = cand.verdict.strip().upper() == ev.verdict.strip().upper()

        return ReplayComparison(
            decision_id=ev.decision_id,
            symbol=ev.symbol,
            horizon_hint=ev.horizon_hint,
            incumbent_verdict=ev.verdict,
            incumbent_confidence=ev.confidence,
            incumbent_excess_return=incumbent_excess,
            incumbent_correct=incumbent_correct,
            candidate_verdict=cand.verdict,
            candidate_confidence=cand.confidence,
            candidate_correct=candidate_correct,
            agree=agree,
            error=err,
        )

    rows = await asyncio.gather(*[_one(ev) for ev in events])
    report.rows = list(rows)

    # Aggregate
    report.n_replayed = sum(1 for r in rows if not r.error)
    report.n_errors = sum(1 for r in rows if r.error)
    report.n_agree = sum(1 for r in rows if r.agree is True)

    inc_labelled = [r for r in rows if r.incumbent_correct is not None]
    cand_labelled = [r for r in rows if r.candidate_correct is not None]
    both_labelled = [
        r for r in rows
        if r.incumbent_correct is not None and r.candidate_correct is not None
    ]
    report.n_both_labelled = len(both_labelled)

    if inc_labelled:
        report.incumbent_hit_rate = (
            sum(1 for r in inc_labelled if r.incumbent_correct) / len(inc_labelled)
        )
    if cand_labelled:
        report.candidate_hit_rate = (
            sum(1 for r in cand_labelled if r.candidate_correct) / len(cand_labelled)
        )
    if report.incumbent_hit_rate is not None and report.candidate_hit_rate is not None:
        report.delta_hit_rate = report.candidate_hit_rate - report.incumbent_hit_rate

    excess_vals = [
        r.incumbent_excess_return for r in rows if r.incumbent_excess_return is not None
    ]
    if excess_vals:
        report.incumbent_mean_excess_return = mean(excess_vals)

    return report
