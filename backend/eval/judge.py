"""
Offline TEVV judges (Phase C1 + C2).

Two separate, pinned prompts evaluate one chat turn:

  * **C1 — trajectory quality** (:func:`score_trajectory`):
    relevance, progression, signal-to-noise, coverage; flags
    ``shortcut_collapse_detected`` and ``loop_or_repetition_detected``.
  * **C2 — answer quality + grounding** (:func:`score_answer`):
    risk awareness, grounding, ``grounding_ratio``,
    ``unsupported_claim_count``, ``final_answer_evidence_refs``.

Both judges are gated behind ``TEVV_LLM_JUDGE=1`` so production chat latency
is unaffected; the harness itself remains deterministic when the flag is
off (TEVV ``llm_judge`` cases skip with a ``_SkipCase``).

The prompt files live under :data:`PROMPTS_DIR` and their version constants
are pinned by filename — the calibration gate documented in the plan
requires that any prompt edit bumps both the constant and the filename so
nightly judge scores remain comparable across runs.

CALIBRATION GATE — judges MUST NOT be ingested by CORAL/SEPL until:
  1. variance across repeated runs on a fixture set is < 0.05 (mean
     absolute deviation of ``trajectory_quality_score`` /
     ``answer_quality_score``),
  2. a human spot-check confirms ``shortcut_collapse_detected``
     correlates with the reviewer's judgement on a sample of n>=20,
  3. the ``prompt_version`` and ``judge_model`` fields in the output
     identify the exact prompt and model used.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROMPTS_DIR: Path = Path(__file__).resolve().parent / "prompts"

TRAJECTORY_PROMPT_VERSION: str = "trajectory_judge_v1"
ANSWER_PROMPT_VERSION: str = "answer_judge_v1"

_PROMPT_FILES: dict[str, str] = {
    "trajectory": "trajectory_judge_v1.txt",
    "answer": "answer_judge_v1.txt",
}

_PROMPT_FILES_V2: dict[str, str] = {
    "trajectory": "trajectory_judge_v2.txt",
    "answer": "answer_judge_v2.txt",
}


def load_prompt(kind: str) -> str:
    """Return the pinned prompt text for ``kind`` in ``{trajectory, answer}``."""
    if kind not in _PROMPT_FILES:
        raise ValueError(f"unknown judge kind {kind!r}")
    use_v2 = os.environ.get("TEVV_JUDGE_PROMPT_V2", "0").strip().lower() in ("1", "true", "yes")
    files = _PROMPT_FILES_V2 if use_v2 else _PROMPT_FILES
    path = PROMPTS_DIR / files[kind]
    if not path.is_file():
        raise FileNotFoundError(f"missing pinned judge prompt: {path}")
    return path.read_text(encoding="utf-8")


def get_prompt_version(kind: str) -> str:
    use_v2 = os.environ.get("TEVV_JUDGE_PROMPT_V2", "0").strip().lower() in ("1", "true", "yes")
    if kind == "trajectory":
        return "trajectory_judge_v2" if use_v2 else TRAJECTORY_PROMPT_VERSION
    return "answer_judge_v2" if use_v2 else ANSWER_PROMPT_VERSION


# ── Strict-JSON parser tolerant of accidental fences ────────────────────────


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _parse_strict_json(text: str) -> dict[str, Any]:
    """
    Defend against models that wrap JSON in markdown fences despite the
    ``Output STRICT JSON only`` instruction.
    """
    s = (text or "").strip()
    if not s:
        raise ValueError("empty judge response")
    if s.startswith("```"):
        s = _JSON_FENCE_RE.sub("", s).strip()
    return json.loads(s)


def _coerce_float(value: Any, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f < lo:
        return lo
    if f > hi:
        return hi
    return f


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── LLM call dispatch ────────────────────────────────────────────────────────


async def _ainvoke(llm, prompt: str) -> str:
    """Minimal text invocation against the project LLM client.

    Allows test injection: any object with ``async def chat_complete(prompt)``
    OR a callable ``llm(prompt)`` returning a string is acceptable.
    """
    if hasattr(llm, "chat_complete"):
        return await llm.chat_complete(prompt)
    if callable(llm):
        out = llm(prompt)
        # Allow sync stubs in tests.
        return out  # type: ignore[return-value]
    raise TypeError("LLM stub must expose chat_complete(prompt) or be callable")


def _resolve_llm(llm: Any | None) -> Any:
    """Return the caller-supplied stub or fall back to the chat client."""
    if llm is not None:
        return llm
    try:
        from backend.deps import llm_client as _default_llm

        return _default_llm
    except Exception:
        return None


def _judge_model_label(llm: Any) -> str:
    if llm is None:
        return "unknown"
    name = getattr(llm, "judge_model_name", None)
    if name:
        return str(name)
    return type(llm).__name__


# ── C1 — trajectory judge ────────────────────────────────────────────────────


def _build_trajectory_prompt(
    *,
    user_message: str,
    tool_trace: list[dict[str, Any]],
    evidence_contract: dict[str, Any],
) -> str:
    body = json.dumps(
        {
            "user_message": user_message,
            "tool_trace": tool_trace,
            "evidence_contract": evidence_contract,
        },
        default=str,
        indent=2,
    )
    return f"{load_prompt('trajectory')}\n\nINPUT:\n{body}\n"


def _normalize_trajectory_result(
    raw: dict[str, Any], judge_model: str
) -> dict[str, Any]:
    dims = raw.get("dimensions") or {}
    norm_dims = {
        "relevance": _coerce_float(dims.get("relevance")),
        "progression": _coerce_float(dims.get("progression")),
        "signal_to_noise": _coerce_float(dims.get("signal_to_noise")),
        "coverage": _coerce_float(dims.get("coverage")),
    }
    score = _coerce_float(raw.get("trajectory_quality_score"))
    if score == 0.0 and any(v > 0.0 for v in norm_dims.values()):
        score = sum(norm_dims.values()) / 4.0
    return {
        "prompt_version": TRAJECTORY_PROMPT_VERSION,
        "judge_model": judge_model,
        "trajectory_quality_score": score,
        "dimensions": norm_dims,
        "shortcut_collapse_detected": bool(raw.get("shortcut_collapse_detected")),
        "loop_or_repetition_detected": bool(raw.get("loop_or_repetition_detected")),
        "reasoning": str(raw.get("reasoning") or "")[:1000],
    }


async def score_trajectory_async(
    *,
    user_message: str,
    tool_trace: list[dict[str, Any]],
    evidence_contract: dict[str, Any],
    llm: Optional[Any] = None,
) -> dict[str, Any]:
    target = _resolve_llm(llm)
    if target is None:
        raise RuntimeError("no LLM client available for trajectory judge")
    prompt = _build_trajectory_prompt(
        user_message=user_message,
        tool_trace=tool_trace,
        evidence_contract=evidence_contract,
    )
    text = await _ainvoke(target, prompt)
    raw = _parse_strict_json(text)
    return _normalize_trajectory_result(raw, _judge_model_label(target))


def score_trajectory(
    *,
    user_message: str,
    tool_trace: list[dict[str, Any]],
    evidence_contract: dict[str, Any],
    llm: Optional[Any] = None,
) -> dict[str, Any]:
    import asyncio

    return asyncio.run(
        score_trajectory_async(
            user_message=user_message,
            tool_trace=tool_trace,
            evidence_contract=evidence_contract,
            llm=llm,
        )
    )


# ── C2 — answer + grounding judge ────────────────────────────────────────────


def _build_answer_prompt(
    *,
    user_message: str,
    final_answer: str,
    source_refs: list[str],
    evidence_contract: dict[str, Any],
) -> str:
    body = json.dumps(
        {
            "user_message": user_message,
            "final_answer": final_answer,
            "source_refs": source_refs,
            "evidence_contract": evidence_contract,
        },
        default=str,
        indent=2,
    )
    return f"{load_prompt('answer')}\n\nINPUT:\n{body}\n"


def _normalize_answer_result(raw: dict[str, Any], judge_model: str) -> dict[str, Any]:
    dims = raw.get("dimensions") or {}
    norm_dims = {
        "risk_awareness": _coerce_float(dims.get("risk_awareness")),
        "grounding": _coerce_float(dims.get("grounding")),
    }
    grounding_ratio = _coerce_float(raw.get("grounding_ratio"))
    score = _coerce_float(raw.get("answer_quality_score"))
    if score == 0.0 and any(v > 0.0 for v in norm_dims.values()):
        score = (sum(norm_dims.values()) / 2.0) * grounding_ratio
    return {
        "prompt_version": ANSWER_PROMPT_VERSION,
        "judge_model": judge_model,
        "answer_quality_score": score,
        "dimensions": norm_dims,
        "grounding_ratio": grounding_ratio,
        "unsupported_claim_count": _coerce_int(raw.get("unsupported_claim_count")),
        "final_answer_evidence_refs": [
            str(r) for r in (raw.get("final_answer_evidence_refs") or [])
        ],
        "reasoning": str(raw.get("reasoning") or "")[:1000],
    }


async def score_answer_async(
    *,
    user_message: str,
    final_answer: str,
    source_refs: list[str],
    evidence_contract: dict[str, Any],
    llm: Optional[Any] = None,
) -> dict[str, Any]:
    target = _resolve_llm(llm)
    if target is None:
        raise RuntimeError("no LLM client available for answer judge")
    prompt = _build_answer_prompt(
        user_message=user_message,
        final_answer=final_answer,
        source_refs=source_refs,
        evidence_contract=evidence_contract,
    )
    text = await _ainvoke(target, prompt)
    raw = _parse_strict_json(text)
    return _normalize_answer_result(raw, _judge_model_label(target))


def score_answer(
    *,
    user_message: str,
    final_answer: str,
    source_refs: list[str],
    evidence_contract: dict[str, Any],
    llm: Optional[Any] = None,
) -> dict[str, Any]:
    import asyncio

    return asyncio.run(
        score_answer_async(
            user_message=user_message,
            final_answer=final_answer,
            source_refs=source_refs,
            evidence_contract=evidence_contract,
            llm=llm,
        )
    )


# ── Calibration gate (documented; CORAL stays rule-based until satisfied) ───


def is_calibration_gate_satisfied() -> bool:
    """
    Return whether the calibration gate documented above has been satisfied.

    Until manual sign-off lands (and a future commit flips
    ``CORAL_INGEST_JUDGE_SCORES=1``), this returns ``False`` so
    :mod:`backend.coral_dreaming` keeps using rule-based aggregations.
    """
    return os.environ.get("CORAL_INGEST_JUDGE_SCORES", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


__all__ = [
    "ANSWER_PROMPT_VERSION",
    "PROMPTS_DIR",
    "TRAJECTORY_PROMPT_VERSION",
    "is_calibration_gate_satisfied",
    "load_prompt",
    "get_prompt_version",
    "score_answer",
    "score_answer_async",
    "score_trajectory",
    "score_trajectory_async",
]
