"""
Operational replay service (Phase 1.3 + Phase 5 gate).

Promotes :mod:`backend.model_swap_replay` from a test-only orchestrator into
an operator tool:

* named candidates ("stub", "llm", "llm:<role>", "baseline_forecast",
  "timesfm_service") resolved to backend adapters,
* reports persisted into the decisions SQLite DB (``harness_replay_reports``)
  so promotion decisions leave an audit trail next to the ledger itself,
* :func:`champion_challenger_gate` — the rule every candidate must pass
  before an operator flips model env vars / deploys new weights.

Nothing here mutates production model configuration; the gate only *informs*
promotion. That keeps the kill-switch story simple: worst case, a bad replay
report is ignored.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from backend import decision_ledger as _dl
from backend.model_swap_replay import ReplayReport, run_replay

from .backend_protocol import (
    BaselineEnsembleForecastBackend,
    LLMVerdictBackend,
    StubVerdictBackend,
    TimesFMServiceForecastBackend,
    forecast_candidate_runner,
    verdict_candidate_runner,
)

logger = logging.getLogger(__name__)

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS harness_replay_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL NOT NULL,
    candidate     TEXT NOT NULL,
    horizon       TEXT NOT NULL,
    decision_type TEXT,
    n_considered  INTEGER,
    n_replayed    INTEGER,
    n_errors      INTEGER,
    incumbent_hit_rate REAL,
    candidate_hit_rate REAL,
    delta_hit_rate     REAL,
    n_both_labelled    INTEGER,
    gate_passed   INTEGER,
    gate_reason   TEXT,
    report_json   TEXT
)
"""


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str


def champion_challenger_gate(
    report: ReplayReport,
    *,
    min_labelled: int = 20,
    min_delta: float = 0.0,
) -> GateResult:
    """Promotion rule: candidate must beat the incumbent on enough graded rows.

    * ``min_labelled``  — both-labelled sample floor; below it the comparison
      is statistically meaningless and the gate fails closed.
    * ``min_delta``     — required hit-rate improvement (e.g. 0.02 = +2 pts).
    """
    if report.n_both_labelled < min_labelled:
        return GateResult(
            False,
            f"insufficient labelled sample: {report.n_both_labelled} < {min_labelled}",
        )
    if report.candidate_hit_rate is None or report.incumbent_hit_rate is None:
        return GateResult(False, "hit rates unavailable")
    delta = report.candidate_hit_rate - report.incumbent_hit_rate
    if delta < min_delta:
        return GateResult(
            False,
            f"delta_hit_rate {delta:+.4f} below required {min_delta:+.4f}",
        )
    return GateResult(True, f"candidate beats incumbent by {delta:+.4f} on n={report.n_both_labelled}")


# ── Candidate resolution ─────────────────────────────────────────────────────


def resolve_candidate_runner(candidate: str):
    """Map a candidate name to ``(runner, label)``.

    Names:
    * ``stub`` / ``stub:<verdict>``  — offline plumbing check
    * ``llm`` / ``llm:<role>``       — currently configured LLM via that role
    * ``baseline_forecast``          — statistical ensemble (price_forecast replays)
    * ``timesfm_service``            — deployed TimesFM HTTP service
    """
    c = (candidate or "stub").strip().lower()
    if c.startswith("stub"):
        verdict = c.split(":", 1)[1].upper().replace("_", " ") if ":" in c else "HOLD"
        backend = StubVerdictBackend(verdict)
        return verdict_candidate_runner(backend), backend.name
    if c.startswith("llm"):
        role = c.split(":", 1)[1] if ":" in c else "swarm_synthesizer"
        backend = LLMVerdictBackend(role=role)
        return verdict_candidate_runner(backend), backend.name
    if c == "baseline_forecast":
        fb = BaselineEnsembleForecastBackend()
        return forecast_candidate_runner(fb), fb.name
    if c == "timesfm_service":
        tb = TimesFMServiceForecastBackend()
        return forecast_candidate_runner(tb), tb.name
    raise ValueError(f"unknown candidate: {candidate!r}")


# ── Run + persist ────────────────────────────────────────────────────────────


def _conn():
    try:
        ledger = _dl.get_ledger()
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception:
        return None
    return conn


def _ensure_table() -> bool:
    conn = _conn()
    if conn is None:
        return False
    try:
        conn.executescript(_TABLE_DDL)
        return True
    except Exception as e:
        logger.warning("[HarnessReplay] table install failed: %s", e)
        return False


def store_report(
    report: ReplayReport,
    gate: GateResult,
    *,
    decision_type: Optional[str],
    include_rows: bool = False,
) -> Optional[int]:
    """Persist a replay report next to the ledger; returns the row id."""
    if not _ensure_table():
        return None
    conn = _conn()
    if conn is None:
        return None
    payload = report.as_dict()
    if not include_rows:
        payload["rows"] = payload["rows"][:50]  # cap row detail for storage
    try:
        cur = conn.execute(
            """INSERT INTO harness_replay_reports
               (created_at, candidate, horizon, decision_type, n_considered,
                n_replayed, n_errors, incumbent_hit_rate, candidate_hit_rate,
                delta_hit_rate, n_both_labelled, gate_passed, gate_reason,
                report_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                report.candidate_model,
                report.horizon,
                decision_type or "",
                report.n_considered,
                report.n_replayed,
                report.n_errors,
                report.incumbent_hit_rate,
                report.candidate_hit_rate,
                report.delta_hit_rate,
                report.n_both_labelled,
                1 if gate.passed else 0,
                gate.reason,
                json.dumps(payload),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    except Exception as e:
        logger.warning("[HarnessReplay] store failed: %s", e)
        return None


def list_reports(limit: int = 20) -> List[Dict[str, Any]]:
    if not _ensure_table():
        return []
    conn = _conn()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """SELECT id, created_at, candidate, horizon, decision_type,
                      n_considered, n_replayed, n_errors, incumbent_hit_rate,
                      candidate_hit_rate, delta_hit_rate, n_both_labelled,
                      gate_passed, gate_reason
               FROM harness_replay_reports
               ORDER BY id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except Exception as e:
        logger.warning("[HarnessReplay] list failed: %s", e)
        return []
    return [dict(r) for r in rows]


async def run_named_replay(
    candidate: str,
    *,
    horizon: str = "5d",
    decision_type: Optional[str] = None,
    since_days: float = 90.0,
    limit: int = 100,
    min_labelled: int = 20,
    min_delta: float = 0.0,
    persist: bool = True,
) -> Tuple[ReplayReport, GateResult, Optional[int]]:
    """Resolve candidate → replay against graded history → gate → persist."""
    runner, label = resolve_candidate_runner(candidate)
    since_ts = time.time() - max(0.0, float(since_days)) * 86400.0
    report = await run_replay(
        runner,
        horizon=horizon,
        decision_type=decision_type,
        since_ts=since_ts,
        limit=limit,
        candidate_model=label,
        max_concurrency=int(os.getenv("HARNESS_REPLAY_CONCURRENCY", "4")),
    )
    gate = champion_challenger_gate(
        report, min_labelled=min_labelled, min_delta=min_delta,
    )
    row_id = store_report(report, gate, decision_type=decision_type) if persist else None
    logger.info(
        "[HarnessReplay] candidate=%s horizon=%s n=%d delta=%s gate=%s",
        label, horizon, report.n_replayed, report.delta_hit_rate, gate.passed,
    )
    return report, gate, row_id
