"""
Chat trajectory telemetry (Phase A1).

Builds training-safe ``TrajectoryStep v2`` records around every chat tool
invocation in :func:`backend.llm_client.LLMClient.stream_chat_plain` and
summarizes the resulting trace for the SSE evidence contract and the
``coral_handoff_events`` ``handoff_chat_trace`` row. The schema separates:

  * execution status (ok | error) from evidence quality
    (useful | partial | irrelevant | unknown),
  * model-authored action summaries from exogenous tool observations
    (so future SEPL / training exports can mask observations from the loss),
  * the *first* step of an active error streak from the *trigger* step where
    the streak reaches ``FATAL_K = 3``.

All helpers are pure and deterministic. The accumulator keeps the state needed
across multiple tool rounds inside a single chat turn.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .chat_tool_family import (
    MemoryNamespace,
    SkillName,
    SkillTier,
    StepPhase,
    ToolFamily,
    get_artifact_type,
    get_tool_family,
    get_tool_namespace,
    get_tool_retrieval_mode,
    make_source_refs,
    make_source_refs_v2,
)

logger = logging.getLogger(__name__)

# Plan: K=3 consecutive execution-class errors mark a trace as fatal.
FATAL_K: int = 3

# TrajectoryStep schema version — distinct from ``evidence_contract.schema_version``.
# Phase E0 bumped to 3 to add skill_name/skill_tier, phase, memory_namespace,
# retrieval_mode, and the typed ``source_refs_v2`` shape.
TRAJECTORY_SCHEMA_VERSION: int = 3

# Bounded sizes for the rich row fields.
PUBLIC_OBSERVATION_CAP: int = 240
DB_OBSERVATION_CAP: int = 600
PUBLIC_INPUT_SUMMARY_CAP: int = 120

# Coral handoff JSON cap is ~24000 chars (see backend.coral_hub.log_handoff_event).
# We leave headroom for SQLite write overhead and future schema additions.
HANDOFF_PAYLOAD_BUDGET: int = 22000

# Event type for ``coral_handoff_events`` rows produced by chat turns.
EVENT_CHAT_TRACE: str = "handoff_chat_trace"


# ── Execution → evidence-quality mapping ─────────────────────────────────────
#
# ``classify_tool_result`` returns one of {success, empty, error}; we map those
# to a per-step ``evidence_quality`` label that downstream Phase C judges can
# use without conflating execution failures and weak signals.
EVIDENCE_QUALITY_BY_OUTCOME: dict[str, str] = {
    "success": "useful",
    "empty": "irrelevant",
    "error": "unknown",
}


def derive_evidence_quality(outcome: str) -> str:
    return EVIDENCE_QUALITY_BY_OUTCOME.get(outcome, "unknown")


def canonical_input_hash(tool_name: str, arguments: Optional[dict]) -> str:
    """Deterministic short hash of (tool_name, sorted-keys arguments) for loop counters."""
    blob = json.dumps(
        {"n": str(tool_name or ""), "a": arguments or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _summarize_args(arguments: Optional[dict], cap: int = PUBLIC_INPUT_SUMMARY_CAP) -> str:
    if not arguments:
        return ""
    parts: list[str] = []
    for k, v in arguments.items():
        sv = str(v)
        if len(sv) > 30:
            sv = sv[:27] + "…"
        parts.append(f"{k}={sv}")
    s = ", ".join(parts)
    if len(s) <= cap:
        return s
    return s[: cap - 1] + "…"


_OBS_WHITESPACE_RE = re.compile(r"\s+")


def _condense_observation(text: str, cap: int) -> str:
    s = (text or "").strip()
    s = _OBS_WHITESPACE_RE.sub(" ", s)
    if len(s) <= cap:
        return s
    return s[: cap - 1] + "…"


# ── Step record builder ──────────────────────────────────────────────────────


@dataclass
class TrajectoryStepInputs:
    """Inputs to :func:`build_trajectory_step` (one tool call)."""

    step_index: int
    tool_name: str
    arguments: dict
    result: str
    outcome: str  # "success" | "empty" | "error" — from classify_tool_result
    latency_ms: int
    error_type: Optional[str] = None
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    # Prior counter state (running totals at end of *previous* step).
    prior_consec_errors: int = 0
    prior_active_streak_start: Optional[int] = None
    prior_fatal_streak_start: Optional[int] = None
    prior_fatal_trigger: Optional[int] = None
    name_seen_count: int = 0
    hash_seen_count: int = 0
    # Phase E0/E2 — skill + phase context (defaults keep callers backwards
    # compatible; the accumulator threads real values once classified).
    skill_name: Optional[SkillName] = None
    skill_tier: Optional[SkillTier] = None
    phase: StepPhase = StepPhase.INVESTIGATION


def build_trajectory_step(inp: TrajectoryStepInputs) -> dict[str, Any]:
    """Build the enriched per-step row from prior accumulator state.

    The function is pure. All sticky/active counters are derived from
    ``inp.prior_*`` and returned in the row so the caller can update its
    accumulator state from the row's ``consecutive_tool_errors_after_step``,
    ``fatal_streak_start_step_index``, and ``fatal_trigger_step_index``.
    """
    is_exec_err = inp.outcome == "error"

    # Active streak: resets on any non-error (success OR empty), since plan
    # says "execution_status == ok" — including soft-empty results — resets
    # the fatal counter.
    if is_exec_err:
        active_consec = inp.prior_consec_errors + 1
        active_streak_start = (
            inp.prior_active_streak_start
            if inp.prior_active_streak_start is not None
            else inp.step_index
        )
    else:
        active_consec = 0
        active_streak_start = None

    # Sticky fatal markers: once a trace goes fatal, both indices are frozen
    # for the rest of the trace so the summary always reports the *first*
    # fatal event.
    fatal_trigger = inp.prior_fatal_trigger
    fatal_streak_start = inp.prior_fatal_streak_start
    if fatal_trigger is None and active_consec >= FATAL_K:
        fatal_trigger = inp.step_index
        fatal_streak_start = active_streak_start

    fatal_detected = fatal_trigger is not None
    family = get_tool_family(inp.tool_name)
    refs = make_source_refs(inp.tool_name, inp.arguments)
    refs_v2 = make_source_refs_v2(inp.tool_name, inp.arguments)
    args_summary = _summarize_args(inp.arguments)
    namespace = get_tool_namespace(inp.tool_name)
    retrieval_mode = get_tool_retrieval_mode(inp.tool_name)
    artifact_type = get_artifact_type(inp.tool_name)
    skill_name = (
        inp.skill_name.value if isinstance(inp.skill_name, SkillName) else None
    )
    skill_tier = (
        inp.skill_tier.value if isinstance(inp.skill_tier, SkillTier) else None
    )
    phase_value = (
        inp.phase.value if isinstance(inp.phase, StepPhase) else str(inp.phase or "investigation")
    )

    row: dict[str, Any] = {
        # Identity
        "trace_id": inp.trace_id,
        "session_id": inp.session_id,
        "message_id": inp.message_id,
        "step_index": int(inp.step_index),
        # Tool identity
        "tool_name": inp.tool_name,
        "tool_family": family.value,
        "tool_input_hash": canonical_input_hash(inp.tool_name, inp.arguments),
        "tool_input_summary": args_summary,
        # Phase E0/E2 — skill + phase context per step.
        "skill_name": skill_name,
        "skill_tier": skill_tier,
        "phase": phase_value,
        "memory_namespace": namespace.value,
        "retrieval_mode": retrieval_mode,
        "artifact_type": artifact_type,
        # Execution vs evidence quality (kept separate per plan)
        "execution_status": "error" if is_exec_err else "ok",
        "error_type": inp.error_type,
        "is_execution_error": bool(is_exec_err),
        "is_recoverable_error": False,
        "evidence_quality": derive_evidence_quality(inp.outcome),
        # Refs + observation summary (training-safe split)
        "source_refs": list(refs),
        "source_refs_v2": list(refs_v2),
        "tool_observation_summary": _condense_observation(
            inp.result, PUBLIC_OBSERVATION_CAP
        ),
        "observation_is_exogenous": True,
        "is_observation_masked": True,
        "tool_observation_tokens_ref": None,
        # Model-authored action (single-line template; richer separation deferred)
        "model_action_summary": (
            f"Called {inp.tool_name} with {args_summary or 'no args'}"
        ),
        "model_generated_tokens_ref": None,
        # Cost / timing
        "latency_ms": int(inp.latency_ms),
        "estimated_cost_usd": None,
        "token_count_in": None,
        "token_count_out": None,
        "tool_cost_units": 1,
        # Fatal counters
        "consecutive_tool_errors_after_step": int(active_consec),
        "fatal_detected_after_step": bool(fatal_detected),
        "fatal_streak_start_step_index": fatal_streak_start,
        "fatal_trigger_step_index": fatal_trigger,
        # Loop counters (passive)
        "repeated_tool_call_count": int(inp.name_seen_count),
        "same_input_hash_repeats": int(inp.hash_seen_count),
        "loop_detected": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Backwards-compatible legacy fields consumed by chat.py and tests.
        "name": inp.tool_name,
        "arguments": dict(inp.arguments or {}),
        "outcome": inp.outcome,
    }
    if is_exec_err and inp.result:
        row["error"] = str(inp.result)[:500]
    return row


# ── Accumulator ──────────────────────────────────────────────────────────────


class TrajectoryAccumulator:
    """
    Stateful accumulator owned by ``stream_chat_plain`` for one chat turn.

    Each tool call updates running counters (active error streak, sticky fatal
    markers, repeated-call counters) and emits a fully-formed trajectory row.
    """

    def __init__(
        self,
        *,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        skill_name: Optional[SkillName] = None,
        skill_tier: Optional[SkillTier] = None,
    ) -> None:
        self.trace_id = trace_id
        self.session_id = session_id
        self.message_id = message_id
        # Phase E0 — best-effort initial skill tag; ``set_skill`` below allows
        # the heuristic classifier (Phase E1) to refine the label after the
        # tool loop has produced enough evidence to decide.
        self.skill_name: Optional[SkillName] = skill_name
        self.skill_tier: Optional[SkillTier] = skill_tier
        self._step_index = 0
        self._consec_errors = 0
        self._active_streak_start: Optional[int] = None
        self._fatal_streak_start: Optional[int] = None
        self._fatal_trigger: Optional[int] = None
        self._name_counts: dict[str, int] = {}
        self._hash_counts: dict[str, int] = {}

    @property
    def step_count(self) -> int:
        return self._step_index

    def set_skill(
        self,
        *,
        skill_name: Optional[SkillName] = None,
        skill_tier: Optional[SkillTier] = None,
    ) -> None:
        """Update skill tags (e.g. once Phase E1 classification has run)."""
        if skill_name is not None:
            self.skill_name = skill_name
        if skill_tier is not None:
            self.skill_tier = skill_tier

    def record(
        self,
        *,
        tool_name: str,
        arguments: Optional[dict],
        result: str,
        outcome: str,
        latency_ms: int,
        error_type: Optional[str] = None,
        skill_name: Optional[SkillName] = None,
        skill_tier: Optional[SkillTier] = None,
        phase: StepPhase = StepPhase.INVESTIGATION,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        h = canonical_input_hash(tool_name, args)
        prior_name = self._name_counts.get(tool_name, 0)
        prior_hash = self._hash_counts.get(h, 0)

        row = build_trajectory_step(
            TrajectoryStepInputs(
                step_index=self._step_index,
                tool_name=tool_name,
                arguments=args,
                result=result,
                outcome=outcome,
                latency_ms=latency_ms,
                error_type=error_type,
                trace_id=self.trace_id,
                session_id=self.session_id,
                message_id=self.message_id,
                prior_consec_errors=self._consec_errors,
                prior_active_streak_start=self._active_streak_start,
                prior_fatal_streak_start=self._fatal_streak_start,
                prior_fatal_trigger=self._fatal_trigger,
                name_seen_count=prior_name,
                hash_seen_count=prior_hash,
                skill_name=skill_name if skill_name is not None else self.skill_name,
                skill_tier=skill_tier if skill_tier is not None else self.skill_tier,
                phase=phase,
            )
        )

        # Update counters from the row so the next step sees the latest state.
        self._consec_errors = int(row["consecutive_tool_errors_after_step"])
        if row["is_execution_error"]:
            if self._active_streak_start is None:
                self._active_streak_start = self._step_index
        else:
            self._active_streak_start = None
        self._fatal_streak_start = row.get("fatal_streak_start_step_index")
        self._fatal_trigger = row.get("fatal_trigger_step_index")
        self._step_index += 1
        self._name_counts[tool_name] = prior_name + 1
        self._hash_counts[h] = prior_hash + 1
        return row


# ── Trace summary (for evidence contract + persistence) ──────────────────────


def summarize_trace(
    trajectory_steps: list[dict[str, Any]],
    *,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    quote_card_tickers: Optional[list[str]] = None,
    skill_name: Optional[SkillName] = None,
    skill_tier: Optional[SkillTier] = None,
    final_answer_text: Optional[str] = None,
) -> dict[str, Any]:
    """Compute the turn-level summary used by the evidence contract.

    ``quote_card_tickers`` (the structural prefetch in chat.py) does *not*
    count as a trajectory step (no fatal math, no step_index), but it *does*
    contribute the ``quote`` family to ``tool_families_used`` so anti-shortcut
    eval cases can credit the prefetched evidence.

    Phase E0/E2: also surfaces ``skill_name`` / ``skill_tier`` (when supplied
    by the caller or threaded through the steps) plus the investigation /
    synthesis phase boundary so the evidence contract can advertise it.
    """
    steps = list(trajectory_steps or [])
    families: list[str] = []
    seen: set[str] = set()
    if quote_card_tickers:
        if "quote" not in seen:
            families.append("quote")
            seen.add("quote")

    fatal_detected = False
    fatal_trigger: Optional[int] = None
    fatal_streak_start: Optional[int] = None
    last_step_idx = -1
    refs_all: list[str] = []
    seen_refs: set[str] = set()
    refs_v2_all: list[dict[str, Any]] = []
    seen_refs_v2: set[str] = set()
    namespaces: list[str] = []
    seen_namespaces: set[str] = set()
    artifact_types: list[str] = []
    seen_artifacts: set[str] = set()
    inv_steps = 0
    syn_steps = 0
    step_skill_name: Optional[str] = None
    step_skill_tier: Optional[str] = None

    for s in steps:
        try:
            last_step_idx = max(last_step_idx, int(s.get("step_index", -1)))
        except Exception:
            pass
        fam = str(s.get("tool_family") or "unknown")
        if fam and fam not in seen:
            families.append(fam)
            seen.add(fam)
        if s.get("fatal_detected_after_step"):
            fatal_detected = True
        if fatal_trigger is None and s.get("fatal_trigger_step_index") is not None:
            fatal_trigger = s.get("fatal_trigger_step_index")
            fatal_streak_start = s.get("fatal_streak_start_step_index")
        for r in s.get("source_refs") or []:
            if r and r not in seen_refs:
                refs_all.append(r)
                seen_refs.add(r)
        for rv in s.get("source_refs_v2") or []:
            if not isinstance(rv, dict):
                continue
            rid = str(rv.get("ref_id") or "")
            if rid and rid not in seen_refs_v2:
                refs_v2_all.append(dict(rv))
                seen_refs_v2.add(rid)
        ns = s.get("memory_namespace")
        if ns and ns not in seen_namespaces:
            namespaces.append(str(ns))
            seen_namespaces.add(str(ns))
        at = s.get("artifact_type")
        if at and at not in seen_artifacts:
            artifact_types.append(str(at))
            seen_artifacts.add(str(at))
        ph = str(s.get("phase") or "investigation")
        if ph == StepPhase.SYNTHESIS.value:
            syn_steps += 1
        else:
            inv_steps += 1
        if step_skill_name is None and s.get("skill_name"):
            step_skill_name = str(s.get("skill_name"))
        if step_skill_tier is None and s.get("skill_tier"):
            step_skill_tier = str(s.get("skill_tier"))

    if fatal_detected and fatal_streak_start is not None:
        valid_prefix_steps = int(fatal_streak_start)
    else:
        valid_prefix_steps = max(0, last_step_idx + 1)

    skill_name_out: Optional[str] = (
        skill_name.value if isinstance(skill_name, SkillName) else step_skill_name
    )
    skill_tier_out: Optional[str] = (
        skill_tier.value if isinstance(skill_tier, SkillTier) else step_skill_tier
    )

    grounded = _compute_answer_grounded(
        final_answer_text=final_answer_text,
        refs=refs_all,
        families=families,
        steps=steps,
    )

    investigation_count = len(steps)
    synthesis_step_index = investigation_count

    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "message_id": message_id,
        "trajectory_step_count": len(steps),
        "tool_families_used": families,
        "fatal_detected": bool(fatal_detected),
        "fatal_trigger_step_index": fatal_trigger,
        "fatal_streak_start_step_index": fatal_streak_start,
        "valid_prefix_steps": int(valid_prefix_steps),
        "source_refs_all": refs_all,
        "source_refs_v2_all": refs_v2_all,
        "memory_namespaces_touched": namespaces,
        "artifact_types_used": artifact_types,
        "investigation_step_count": int(investigation_count),
        "synthesis_step_index": int(synthesis_step_index),
        "answer_grounded_to_investigation": grounded,
        "skill_name": skill_name_out,
        "skill_tier": skill_tier_out,
    }


def _compute_answer_grounded(
    *,
    final_answer_text: Optional[str],
    refs: list[str],
    families: list[str],
    steps: list[dict[str, Any]],
) -> bool:
    """Phase E2 conservative grounding heuristic.

    A turn counts as grounded to investigation when at least one source ref
    keyword (ticker, family token, query stem) appears in the final answer.
    A turn with no investigation steps is vacuously grounded — we avoid
    penalising small-talk turns that legitimately need no tools. The proper
    grounding score remains the offline C2 judge's ``grounding_ratio``.
    """
    if not steps:
        return True
    if not final_answer_text:
        return False
    text = str(final_answer_text).lower()
    for ref in refs:
        if not ref:
            continue
        # ``family:key`` — pull out the key half so we look for something
        # recognisable in the answer (e.g. a ticker symbol).
        if ":" in ref:
            tail = ref.split(":", 1)[1]
        else:
            tail = ref
        tail = tail.strip().lower()
        if not tail:
            continue
        if tail in text:
            return True
    for fam in families:
        if fam and fam.lower() in text:
            return True
    return False


# ── Persistence payload (handoff_chat_trace) ─────────────────────────────────


def _redact_step_for_persistence(step: dict[str, Any], observation_cap: int) -> dict[str, Any]:
    out = dict(step)
    obs = out.get("tool_observation_summary") or ""
    if isinstance(obs, str) and len(obs) > observation_cap:
        out["tool_observation_summary"] = obs[: observation_cap - 1] + "…"
    # Drop free-form arguments/error blobs from the persisted copy — the hash
    # and per-tool source_refs preserve enough signal for analytics.
    out.pop("arguments", None)
    out.pop("error", None)
    return out


def _redact_step_for_sse(step: dict[str, Any]) -> dict[str, Any]:
    """Same shape as DB redaction but always strips ``arguments`` for SSE."""
    return _redact_step_for_persistence(step, PUBLIC_OBSERVATION_CAP)


def trajectory_steps_for_sse(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SSE-safe condensed trajectory list — bounded observation, no raw args."""
    return [_redact_step_for_sse(s) for s in (steps or [])]


def build_handoff_payload(
    *,
    summary: dict[str, Any],
    trajectory_steps: list[dict[str, Any]],
    evidence_contract: dict[str, Any],
    cap_bytes: int = HANDOFF_PAYLOAD_BUDGET,
) -> dict[str, Any]:
    """Assemble the bounded JSON payload for ``log_handoff_event``."""
    payload: dict[str, Any] = {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "trace_id": summary.get("trace_id"),
        "session_id": summary.get("session_id"),
        "message_id": summary.get("message_id"),
        "skill_name": summary.get("skill_name"),
        "skill_tier": summary.get("skill_tier"),
        "fatal_detected": bool(summary.get("fatal_detected", False)),
        "fatal_trigger_step_index": summary.get("fatal_trigger_step_index"),
        "fatal_streak_start_step_index": summary.get("fatal_streak_start_step_index"),
        "valid_prefix_steps": int(summary.get("valid_prefix_steps", 0) or 0),
        "trajectory_step_count": int(summary.get("trajectory_step_count", 0) or 0),
        "investigation_step_count": int(summary.get("investigation_step_count", 0) or 0),
        "synthesis_step_index": summary.get("synthesis_step_index"),
        "answer_grounded_to_investigation": bool(
            summary.get("answer_grounded_to_investigation", False)
        ),
        "tools_called": [str(s.get("tool_name") or "") for s in (trajectory_steps or [])],
        "tool_families_used": list(summary.get("tool_families_used") or []),
        "memory_namespaces_touched": list(summary.get("memory_namespaces_touched") or []),
        "artifact_types_used": list(summary.get("artifact_types_used") or []),
        "source_refs": list(summary.get("source_refs_all") or []),
        "source_refs_v2": list(summary.get("source_refs_v2_all") or []),
        "evidence_contract": {
            "schema_version": evidence_contract.get("schema_version"),
            "confidence_band": evidence_contract.get("confidence_band"),
            "abstain_reason": evidence_contract.get("abstain_reason"),
        },
        "trajectory_steps": [
            _redact_step_for_persistence(s, DB_OBSERVATION_CAP)
            for s in (trajectory_steps or [])
        ],
    }
    return _enforce_payload_cap(payload, cap_bytes)


def _enforce_payload_cap(payload: dict[str, Any], cap_bytes: int) -> dict[str, Any]:
    """Truncate observations first, then drop trailing steps until under cap."""
    blob = json.dumps(payload, default=str)
    if len(blob) <= cap_bytes:
        return payload

    steps = payload.get("trajectory_steps") or []
    for s in steps:
        if isinstance(s, dict):
            s["tool_observation_summary"] = ""
    blob = json.dumps(payload, default=str)
    while len(blob) > cap_bytes and steps:
        steps.pop()
        payload["trajectory_steps"] = steps
        blob = json.dumps(payload, default=str)
    return payload


# ── Convenience: log a chat trace handoff event (best-effort) ────────────────


def log_chat_trace_event(
    *,
    summary: dict[str, Any],
    trajectory_steps: list[dict[str, Any]],
    evidence_contract: dict[str, Any],
) -> Optional[int]:
    """
    Persist one ``handoff_chat_trace`` row; never raises.

    Returns the inserted row id on success, or ``None`` when the import or
    insert fails (e.g. tests without a writable DB path). Failures are logged
    at DEBUG so production hot paths stay quiet.
    """
    try:
        from . import coral_hub  # local import: avoid module-load coupling
    except Exception as e:
        logger.debug("[chat_tool_telemetry] coral_hub import failed: %s", e)
        return None
    try:
        payload = build_handoff_payload(
            summary=summary,
            trajectory_steps=trajectory_steps,
            evidence_contract=evidence_contract,
        )
        return int(coral_hub.log_handoff_event(EVENT_CHAT_TRACE, payload))
    except Exception as e:
        logger.debug("[chat_tool_telemetry] log_handoff_event failed: %s", e)
        return None
