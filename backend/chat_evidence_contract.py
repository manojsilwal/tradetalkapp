"""
Layer 1 — structured evidence contract for chat (Phase A2 → E0, schema v4).

Builds a small JSON-serializable payload per assistant turn:

  * Legacy (v2) fields: ``sources_used``, ``tools_called``, ``tool_outcomes``,
    ``confidence_band``, ``abstain_reason``, ``rag_chunk_refs``.
  * Phase A2 (v3) summary fields driven by ``backend.chat_tool_telemetry``:
    ``trace_id``, ``tool_families_used``, ``trajectory_step_count``,
    ``valid_prefix_steps``, ``fatal_detected``, ``fatal_trigger_step_index``,
    ``fatal_streak_start_step_index``, ``trajectory_steps``.
  * Phase E0 (v4) skill + phase metadata: ``skill_name``, ``skill_tier``,
    ``expected_tool_families``, ``investigation_step_count``,
    ``synthesis_step_index``, ``answer_grounded_to_investigation``,
    ``memory_namespaces_touched``, ``artifact_types_used``,
    ``source_refs_v2``.
  * Phase C2 placeholders kept stable so eval / SSE consumers can subscribe
    today without waiting on the offline judges:
    ``final_answer_evidence_refs``, ``grounding_ratio``,
    ``unsupported_claim_count``.

Tool outcomes are classified from handler return strings so the contract
stays deterministic for eval harnesses.
"""
from __future__ import annotations

from typing import Any, Optional


SCHEMA_VERSION: int = 4


def classify_tool_result(result: str) -> str:
    """Return success | empty | error for a tool handler string result."""
    s = (result or "").strip()
    if not s:
        return "empty"
    head = s[:120].lower()
    if s.startswith("Error ") or s.startswith("Error fetching") or "error executing" in head:
        return "error"
    if "invalid ticker" in head or "invalid url" in head or "please provide" in head:
        return "empty"
    if "no price data" in head or "no historical" in head or "no recent news" in head:
        return "empty"
    if "unavailable" in head and ("fincrawler" in head or "scraping" in head or "sec filing" in head):
        return "empty"
    if len(s) < 40 and ("no " in head or "not found" in head or "delisted" in head):
        return "empty"
    return "success"


def _normalize_rag_chunk_refs(raw: Any) -> list[dict[str, Any]]:
    """Coerce the optional ``meta['rag_chunk_refs']`` blob into a list shape."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, dict):
            continue
        chunk_id = str(r.get("chunk_id") or "")
        collection = str(r.get("collection") or "")
        if not chunk_id and not collection:
            continue
        try:
            rank = int(r.get("rank", 0))
        except Exception:
            rank = 0
        try:
            distance = float(r.get("distance", 1.0))
        except Exception:
            distance = 1.0
        out.append(
            {
                "chunk_id": chunk_id,
                "collection": collection,
                "rank": rank,
                "distance": distance,
                "ticker": str(r.get("ticker") or ""),
            }
        )
    return out


def _families_from_tool_trace(
    tool_trace: list[dict[str, Any]],
    quote_card_tickers: list[str],
) -> list[str]:
    """
    Fallback when callers omit ``trajectory_summary`` (e.g. legacy fixtures).

    Mirrors :func:`backend.chat_tool_telemetry.summarize_trace`'s behaviour:
    ``quote_card`` prefetch contributes ``quote`` even when no quote tool was
    called, and family resolution defers to
    :func:`backend.chat_tool_family.get_tool_family` so the same enum drives
    both production and TEVV anti-shortcut checks.
    """
    from .chat_tool_family import get_tool_family

    families: list[str] = []
    seen: set[str] = set()
    if quote_card_tickers:
        if "quote" not in seen:
            families.append("quote")
            seen.add("quote")
    for t in tool_trace or []:
        fam = str(
            t.get("tool_family") or get_tool_family(str(t.get("name", ""))).value
        )
        if fam and fam not in seen:
            families.append(fam)
            seen.add(fam)
    return families


def build_evidence_contract(
    *,
    tool_trace: list[dict[str, Any]],
    quote_card_tickers: list[str],
    meta: dict[str, Any],
    trajectory_summary: Optional[dict[str, Any]] = None,
    trajectory_steps: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Assemble the side-channel contract for one chat turn.

    ``tool_trace`` items still carry the legacy ``{name, outcome, error?}``
    keys *plus* the Phase A1 enrichments (``tool_family``,
    ``execution_status``, ``source_refs``, fatal counters …). Older tests that
    only set the legacy keys keep working.

    ``trajectory_summary``, when supplied, comes from
    :func:`backend.chat_tool_telemetry.summarize_trace` and provides the
    canonical turn-level fields. When omitted, conservative fallbacks derive
    families from the trace/prefetch and treat the trace as non-fatal.
    """
    rag_ok = bool(meta.get("rag_nonempty"))
    coral_ok = bool(meta.get("coral_hub_nonempty"))

    sources_used: list[str] = []
    if rag_ok:
        sources_used.append("internal_kb")
    if coral_ok:
        sources_used.append("coral_hub")

    tool_outcomes: list[dict[str, str]] = []
    for t in tool_trace:
        name = t.get("name") or "unknown"
        oc = t.get("outcome") or "empty"
        tool_outcomes.append({"name": str(name), "outcome": str(oc)})
        if oc == "success":
            sources_used.append(f"tool:{name}")

    for tk in quote_card_tickers:
        if tk:
            sources_used.append(f"quote_card:{tk}")

    tools_called = [str(t.get("name", "")) for t in tool_trace if t.get("name")]

    has_success_tool = any(t.get("outcome") == "success" for t in tool_trace)
    has_quote = bool(quote_card_tickers)
    any_tools = bool(tool_trace)
    all_bad = any_tools and all(t.get("outcome") in ("empty", "error") for t in tool_trace)

    abstain_reason: Optional[str] = None
    if any_tools and all_bad and not has_quote:
        abstain_reason = "all_tools_empty_or_error"

    if has_quote or has_success_tool:
        confidence_band = "high"
    elif all_bad:
        confidence_band = "low"
    else:
        confidence_band = "medium"

    rag_chunk_refs = _normalize_rag_chunk_refs(meta.get("rag_chunk_refs"))

    # ── Phase A2: turn-level summary (B hard-gate fields) ──────────────────
    summary = trajectory_summary or {}
    tool_families_used = list(
        summary.get("tool_families_used")
        or _families_from_tool_trace(tool_trace, quote_card_tickers)
    )
    fatal_detected = bool(summary.get("fatal_detected", False))
    fatal_trigger_step_index = summary.get("fatal_trigger_step_index")
    fatal_streak_start_step_index = summary.get("fatal_streak_start_step_index")
    valid_prefix_steps = summary.get("valid_prefix_steps")
    if valid_prefix_steps is None:
        valid_prefix_steps = len(tool_trace)
    trajectory_step_count = summary.get("trajectory_step_count")
    if trajectory_step_count is None:
        trajectory_step_count = len(tool_trace)
    trace_id = summary.get("trace_id")

    # ── Phase E0: skill + phase + memory namespace metadata ───────────────
    skill_name = summary.get("skill_name")
    skill_tier = summary.get("skill_tier")
    expected_tool_families: list[str] = []
    if skill_name:
        try:
            from .chat_tool_family import SkillName, expected_families_for_skill

            expected_tool_families = expected_families_for_skill(SkillName(skill_name))
        except Exception:
            expected_tool_families = []

    investigation_step_count = summary.get("investigation_step_count")
    if investigation_step_count is None:
        investigation_step_count = int(trajectory_step_count)
    synthesis_step_index = summary.get("synthesis_step_index")
    if synthesis_step_index is None:
        synthesis_step_index = int(investigation_step_count)
    answer_grounded_to_investigation = bool(
        summary.get("answer_grounded_to_investigation", False)
    )
    memory_namespaces_touched = list(summary.get("memory_namespaces_touched") or [])
    artifact_types_used = list(summary.get("artifact_types_used") or [])
    source_refs_v2 = list(summary.get("source_refs_v2_all") or [])

    contract: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id,
        "sources_used": sources_used,
        "tools_called": tools_called,
        "tool_outcomes": tool_outcomes,
        "tool_families_used": tool_families_used,
        "trajectory_step_count": int(trajectory_step_count),
        "valid_prefix_steps": int(valid_prefix_steps),
        "fatal_detected": fatal_detected,
        "fatal_trigger_step_index": fatal_trigger_step_index,
        "fatal_streak_start_step_index": fatal_streak_start_step_index,
        # Phase E0 fields
        "skill_name": skill_name,
        "skill_tier": skill_tier,
        "expected_tool_families": expected_tool_families,
        "investigation_step_count": int(investigation_step_count),
        "synthesis_step_index": int(synthesis_step_index),
        "answer_grounded_to_investigation": answer_grounded_to_investigation,
        "memory_namespaces_touched": memory_namespaces_touched,
        "artifact_types_used": artifact_types_used,
        "source_refs_v2": source_refs_v2,
        # Existing fields
        "confidence_band": confidence_band,
        "abstain_reason": abstain_reason,
        "rag_chunk_refs": rag_chunk_refs,
        # Phase C2 placeholders. The offline answer-quality judge fills these
        # nightly; production hot path leaves them at the safe defaults below
        # so SSE consumers can subscribe to the keys today.
        "final_answer_evidence_refs": [],
        "grounding_ratio": None,
        "unsupported_claim_count": None,
    }
    if trajectory_steps is not None:
        contract["trajectory_steps"] = list(trajectory_steps)
    return contract
