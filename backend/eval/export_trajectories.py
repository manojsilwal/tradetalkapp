"""
Phase D — pre-fatal trajectory export (deferred / gated).

Builds candidate SEPL/training records from the persisted
``handoff_chat_trace`` rows in ``coral_handoff_events``. Every record:

  * keeps the *valid prefix* (steps strictly before
    ``fatal_streak_start_step_index`` when the trace went fatal, otherwise
    every step),
  * preserves the model vs observation separation (``model_action_summary``
    and ``tool_observation_summary`` stay distinct so a future training loop
    can mask exogenous tool returns from the loss),
  * carries the trajectory and answer judge scores when available so
    downstream consumers can filter by the calibration gate.

Activation rules:

  * The CLI entry point is a no-op unless ``TEVV_EXPORT_TRAJECTORIES=1``.
  * The library function :func:`export_pre_fatal_trajectories` works
    regardless of the env flag — it is intended for unit tests and offline
    inspection — but ingestion-into-CORAL/SEPL is gated behind
    :func:`backend.eval.judge.is_calibration_gate_satisfied`.
  * No masked-loss / GRPO export ships here. The plan defers any such
    export until explicit training infrastructure commitment.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Iterable, Optional

from backend.chat_tool_telemetry import EVENT_CHAT_TRACE
from backend.eval.judge import is_calibration_gate_satisfied

logger = logging.getLogger(__name__)

EXPORT_FLAG_ENV: str = "TEVV_EXPORT_TRAJECTORIES"


def _flag_enabled() -> bool:
    return os.environ.get(EXPORT_FLAG_ENV, "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _iter_chat_trace_events(events: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for ev in events or []:
        if (ev or {}).get("event_type") != EVENT_CHAT_TRACE:
            continue
        yield ev


def _pre_fatal_steps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Slice the trajectory at the *first* fatal streak.

    When the trace never went fatal, every step is included. When the
    trace went fatal, we drop everything from ``fatal_streak_start_step_index``
    onwards — those tokens are the post-fatal continuation that the plan's
    masked-loss recipe wants the trainer to ignore.
    """
    steps = list(payload.get("trajectory_steps") or [])
    fatal_detected = bool(payload.get("fatal_detected"))
    if not fatal_detected:
        return steps
    cutoff = payload.get("fatal_streak_start_step_index")
    if cutoff is None:
        return steps
    try:
        cutoff_idx = int(cutoff)
    except (TypeError, ValueError):
        return steps
    return [s for s in steps if int(s.get("step_index", -1)) < cutoff_idx]


def export_pre_fatal_trajectories(
    events: Iterable[dict[str, Any]],
    *,
    only_with_steps: bool = True,
) -> list[dict[str, Any]]:
    """
    Build pre-fatal trajectory records from ``coral_handoff_events`` rows.

    ``events`` is the list returned by
    :func:`backend.coral_hub.list_handoff_events_since`. The function does
    NOT touch the database directly so unit tests can pass synthetic events
    in without touching the writable DB path.
    """
    out: list[dict[str, Any]] = []
    for ev in _iter_chat_trace_events(events):
        payload = ev.get("payload") or {}
        prefix = _pre_fatal_steps(payload)
        if only_with_steps and not prefix:
            continue
        record: dict[str, Any] = {
            "event_id": ev.get("id"),
            "trace_id": payload.get("trace_id"),
            "session_id": payload.get("session_id"),
            "message_id": payload.get("message_id"),
            "skill_name": payload.get("skill_name"),
            "skill_tier": payload.get("skill_tier"),
            "fatal_detected": bool(payload.get("fatal_detected")),
            "fatal_trigger_step_index": payload.get("fatal_trigger_step_index"),
            "fatal_streak_start_step_index": payload.get(
                "fatal_streak_start_step_index"
            ),
            "valid_prefix_steps": payload.get("valid_prefix_steps"),
            "synthesis_step_index": payload.get("synthesis_step_index"),
            "answer_grounded_to_investigation": bool(
                payload.get("answer_grounded_to_investigation", False)
            ),
            "tool_families_used": list(payload.get("tool_families_used") or []),
            "tools_called": list(payload.get("tools_called") or []),
            "evidence_contract": payload.get("evidence_contract") or {},
            # Plan: keep model action / tool observation separation in the
            # export so downstream training can mask exogenous returns.
            "trajectory_prefix": [
                {
                    "step_index": s.get("step_index"),
                    "tool_name": s.get("tool_name"),
                    "tool_family": s.get("tool_family"),
                    "phase": s.get("phase"),
                    "skill_name": s.get("skill_name"),
                    "skill_tier": s.get("skill_tier"),
                    "execution_status": s.get("execution_status"),
                    "evidence_quality": s.get("evidence_quality"),
                    "source_refs": list(s.get("source_refs") or []),
                    "model_action_summary": s.get("model_action_summary"),
                    "tool_observation_summary": s.get("tool_observation_summary"),
                    "is_observation_masked": bool(s.get("is_observation_masked")),
                }
                for s in prefix
            ],
        }
        out.append(record)
    return out


def export_from_db(
    *,
    since_epoch: float = 0.0,
    only_with_steps: bool = True,
) -> list[dict[str, Any]]:
    """Convenience wrapper that reads from the live ``coral_hub``."""
    from backend import coral_hub

    events = coral_hub.list_handoff_events_since(since_epoch=float(since_epoch))
    return export_pre_fatal_trajectories(events, only_with_steps=only_with_steps)


def consume_into_coral(records: list[dict[str, Any]]) -> int:
    """
    Reserved hook for SEPL/CORAL consumption.

    Returns the number of records that *would* have been ingested. Always a
    no-op until the calibration gate (see :mod:`backend.eval.judge`) is
    satisfied — the plan requires judge stability before CORAL writes
    skills. When the gate flips on, this hook becomes the integration point
    for the downstream consumer.
    """
    if not is_calibration_gate_satisfied():
        logger.info(
            "[export_trajectories] skipping CORAL ingest — calibration gate not satisfied"
        )
        return 0
    # Future: forward into coral_dreaming.write_skill / SEPL reflections.
    logger.info(
        "[export_trajectories] calibration gate ON but consumer not yet implemented"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase D — pre-fatal trajectory export (gated)"
    )
    parser.add_argument("--since-epoch", type=float, default=0.0)
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="include records whose pre-fatal prefix is empty",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="forward records to CORAL/SEPL (still respects the calibration gate)",
    )
    args = parser.parse_args()

    if not _flag_enabled():
        print(
            f"[export_trajectories] {EXPORT_FLAG_ENV}=0 — skeleton is gated OFF; "
            "set the env flag to enable, then opt into ingest with --ingest "
            "after the calibration gate (CORAL_INGEST_JUDGE_SCORES=1) is satisfied.",
            file=sys.stderr,
        )
        return 0

    records = export_from_db(
        since_epoch=args.since_epoch,
        only_with_steps=not args.include_empty,
    )
    json.dump(
        {"count": len(records), "records": records},
        sys.stdout,
        default=str,
        indent=2,
    )
    sys.stdout.write("\n")

    if args.ingest:
        n = consume_into_coral(records)
        print(f"[export_trajectories] ingested {n} records", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
