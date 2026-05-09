"""
Run TEVV chat eval cases from case_bank.json (deterministic; no live LLM required).

Four scoring axes (reported in JSON):
  - direction_accuracy — routing/intent/tool outcome alignment
  - json_validity — evidence contract shape and expected fields
  - shortcut_resistance — Phase B anti-shortcut family/coverage assertions
  - reasoning_quality — optional LLM-as-judge (skipped unless TEVV_LLM_JUDGE=1)

Usage:
  PYTHONPATH=. python -m backend.eval.tevv_runner
  PYTHONPATH=. python -m backend.eval.tevv_runner --json   # machine-readable summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from backend.chat_evidence_contract import build_evidence_contract, classify_tool_result
from backend.chat_tool_telemetry import summarize_trace
from backend.routers.chat import (
    _extract_quote_ticker,
    _mover_query_intent,
    _wants_live_quote,
)

CASE_BANK_PATH = Path(__file__).resolve().parent / "case_bank.json"

REQUIRED_EVIDENCE_KEYS = frozenset(
    {
        # Legacy (v2) keys preserved for backward compatibility.
        "schema_version",
        "sources_used",
        "tools_called",
        "tool_outcomes",
        "confidence_band",
        "abstain_reason",
        # Phase A2 (v3) summary fields — Phase B anti-shortcut hard gate.
        "trace_id",
        "tool_families_used",
        "trajectory_step_count",
        "valid_prefix_steps",
        "fatal_detected",
        "fatal_trigger_step_index",
        "fatal_streak_start_step_index",
        # Phase E0 (v4) skill + phase + namespace surface.
        "skill_name",
        "skill_tier",
        "expected_tool_families",
        "investigation_step_count",
        "synthesis_step_index",
        "answer_grounded_to_investigation",
        "memory_namespaces_touched",
        "artifact_types_used",
        "source_refs_v2",
    }
)


def _validate_evidence_schema(contract: dict[str, Any], *, expect_valid: bool) -> None:
    keys = set(contract.keys())
    if expect_valid:
        missing = REQUIRED_EVIDENCE_KEYS - keys
        assert not missing, f"missing keys: {sorted(missing)}"
        assert isinstance(contract.get("schema_version"), int)
        assert isinstance(contract.get("sources_used"), list)
        assert isinstance(contract.get("tools_called"), list)
        assert isinstance(contract.get("tool_outcomes"), list)
        assert contract.get("confidence_band") in ("high", "medium", "low")
    else:
        missing = REQUIRED_EVIDENCE_KEYS - keys
        assert missing, f"expected invalid contract but all keys present: {keys}"


def _execute_check(case: dict[str, Any]) -> None:
    check = case["check"]
    inp = case.get("input") or {}
    exp = case.get("expect") or {}

    if check == "mover_intent":
        got = _mover_query_intent(inp["message"])
        want = exp.get("intent")
        if want is None:
            assert got is None, f"expected None, got {got!r}"
        else:
            assert got == want, f"expected intent {want!r}, got {got!r}"

    elif check == "wants_quote":
        got = _wants_live_quote(inp["message"])
        assert got == exp["wants"], f"expected wants_quote={exp['wants']}, got {got}"

    elif check == "quote_ticker":
        got = _extract_quote_ticker(inp["message"])
        if exp.get("ticker") is None:
            assert got is None, f"expected no ticker, got {got!r}"
        else:
            assert got == exp["ticker"], f"expected ticker {exp['ticker']!r}, got {got!r}"

    elif check == "classify_tool":
        got = classify_tool_result(inp["result"])
        assert got == exp["outcome"], f"expected outcome {exp['outcome']!r}, got {got!r}"

    elif check == "evidence_contract":
        got = build_evidence_contract(
            tool_trace=list(inp.get("tool_trace") or []),
            quote_card_tickers=list(inp.get("quote_card_tickers") or []),
            meta=dict(inp.get("meta") or {}),
        )
        if "confidence_band" in exp:
            assert got["confidence_band"] == exp["confidence_band"], (
                f"confidence_band: want {exp['confidence_band']!r}, got {got['confidence_band']!r}"
            )
        if "abstain_reason" in exp:
            assert got["abstain_reason"] == exp["abstain_reason"], (
                f"abstain_reason: want {exp['abstain_reason']!r}, got {got['abstain_reason']!r}"
            )
        if "sources_used_contains" in exp:
            for s in exp["sources_used_contains"]:
                assert s in got["sources_used"], f"sources_used should contain {s!r}, got {got['sources_used']}"

    elif check == "evidence_schema":
        contract = dict(inp.get("contract") or {})
        _validate_evidence_schema(contract, expect_valid=exp.get("valid", True))

    elif check == "anti_shortcut":
        _execute_anti_shortcut_check(inp, exp)

    elif check == "llm_judge":
        if os.environ.get("TEVV_LLM_JUDGE", "").strip().lower() not in ("1", "true", "yes"):
            raise _SkipCase("TEVV_LLM_JUDGE not enabled")
        _execute_llm_judge_check(inp, exp)

    else:
        raise ValueError(f"unknown check type: {check!r}")


def _execute_anti_shortcut_check(inp: dict[str, Any], exp: dict[str, Any]) -> None:
    """Phase B: assert tool-family coverage / shortcut detection on a synthetic trace.

    The case provides a synthetic ``tool_trace`` (and optional
    ``quote_card_tickers`` / ``meta``); we route it through the live
    ``summarize_trace`` + ``build_evidence_contract`` so the assertions match
    the production behaviour exactly. ``tool_family`` is resolved through the
    canonical :mod:`backend.chat_tool_family` registry.
    """
    tool_trace = list(inp.get("tool_trace") or [])
    quote_card_tickers = list(inp.get("quote_card_tickers") or [])
    meta = dict(inp.get("meta") or {})

    summary = summarize_trace(
        tool_trace,
        trace_id=inp.get("trace_id"),
        session_id=inp.get("session_id"),
        message_id=inp.get("message_id"),
        quote_card_tickers=quote_card_tickers,
    )
    contract = build_evidence_contract(
        tool_trace=tool_trace,
        quote_card_tickers=quote_card_tickers,
        meta=meta,
        trajectory_summary=summary,
    )

    families = list(contract.get("tool_families_used") or [])
    fams_set = set(families)

    if "source_families_min" in exp:
        for fam in exp["source_families_min"]:
            assert fam in fams_set, (
                f"missing required source family {fam!r}; got {sorted(fams_set)}"
            )

    if "min_tool_calls" in exp:
        n = len(contract.get("tools_called") or [])
        assert n >= int(exp["min_tool_calls"]), (
            f"min_tool_calls={exp['min_tool_calls']}, got {n}"
        )

    if exp.get("disallow_single_family_answer"):
        assert len(fams_set) > 1, (
            f"single-family answer detected: {sorted(fams_set)}"
        )

    if "fail_if_only" in exp:
        bad = set(exp["fail_if_only"])
        assert fams_set != bad, (
            f"agent collapsed to a single family {sorted(bad)}; got {sorted(fams_set)}"
        )

    if exp.get("required_evidence_refs"):
        nonempty = bool(
            (contract.get("rag_chunk_refs") or [])
            or any(s.startswith("tool:") for s in (contract.get("sources_used") or []))
            or any(s.startswith("quote_card:") for s in (contract.get("sources_used") or []))
        )
        assert nonempty, "required_evidence_refs but contract has none"

    if "fatal_detected" in exp:
        got_fatal = bool(contract.get("fatal_detected"))
        assert got_fatal == bool(exp["fatal_detected"]), (
            f"fatal_detected: want {exp['fatal_detected']!r}, got {got_fatal!r}"
        )

    if "tool_families_eq" in exp:
        assert sorted(fams_set) == sorted(exp["tool_families_eq"]), (
            f"tool_families: want {sorted(exp['tool_families_eq'])}, got {sorted(fams_set)}"
        )

    if "collapse_detected" in exp:
        got_collapse = len(fams_set) <= 1
        assert got_collapse == bool(exp["collapse_detected"]), (
            f"collapse_detected: want {exp['collapse_detected']!r}, got {got_collapse!r}"
        )

    if "skill_name_detected" in exp:
        got_skill = str(contract.get("skill_name") or "")
        assert got_skill == str(exp["skill_name_detected"]), (
            f"skill_name_detected: want {exp['skill_name_detected']!r}, got {got_skill!r}"
        )

    if exp.get("risk_family_present"):
        assert "risk" in fams_set, f"risk family missing; got {sorted(fams_set)}"

    if "answer_grounded_to_investigation" in exp:
        got_grounded = bool(contract.get("answer_grounded_to_investigation"))
        assert got_grounded == bool(exp["answer_grounded_to_investigation"]), (
            "answer_grounded_to_investigation: "
            f"want {exp['answer_grounded_to_investigation']!r}, got {got_grounded!r}"
        )

    if exp.get("phase_boundary_respected"):
        inv = int(contract.get("investigation_step_count") or 0)
        synth = int(contract.get("synthesis_step_index") or 0)
        total = int(contract.get("trajectory_step_count") or 0)
        assert inv == total, (
            f"phase boundary invalid: investigation_step_count={inv}, total={total}"
        )
        assert synth == inv, (
            f"phase boundary invalid: synthesis_step_index={synth}, investigation={inv}"
        )


def _execute_llm_judge_check(inp: dict[str, Any], exp: dict[str, Any]) -> None:
    """Phase C: dispatch to the trajectory or answer judge.

    Cases set ``input.kind`` to ``trajectory`` or ``answer``. The judge calls
    are gated by ``TEVV_LLM_JUDGE=1`` (handled by the caller); this function
    additionally honors ``TEVV_LLM_JUDGE_DRY=1`` for hermetic CI runs that
    must not call any external LLM. Dry-run mode validates only that the
    pinned prompt files load and that the case fixtures expose the required
    inputs.
    """
    from backend.eval import judge as _judge

    kind = str(inp.get("kind") or "trajectory").strip().lower()
    if kind not in ("trajectory", "answer"):
        raise _SkipCase(f"unknown llm_judge kind {kind!r}")

    dry = os.environ.get("TEVV_LLM_JUDGE_DRY", "0").strip().lower() in ("1", "true", "yes")
    if dry:
        prompt = _judge.load_prompt(kind)
        assert prompt and "JSON" in prompt, "judge prompt missing or malformed"
        # Validate fixture shape so live runs won't trip later.
        if kind == "trajectory":
            assert "tool_trace" in inp and "user_message" in inp, (
                "trajectory judge fixture must include tool_trace and user_message"
            )
        else:
            assert "final_answer" in inp and "user_message" in inp, (
                "answer judge fixture must include final_answer and user_message"
            )
        return

    if kind == "trajectory":
        result = _judge.score_trajectory(
            user_message=str(inp.get("user_message") or ""),
            tool_trace=list(inp.get("tool_trace") or []),
            evidence_contract=dict(inp.get("evidence_contract") or {}),
        )
    else:
        result = _judge.score_answer(
            user_message=str(inp.get("user_message") or ""),
            final_answer=str(inp.get("final_answer") or ""),
            source_refs=list(inp.get("source_refs") or []),
            evidence_contract=dict(inp.get("evidence_contract") or {}),
        )

    # The judge's score is non-deterministic; a fixture may pin a minimum to
    # catch obvious regressions on calibrated rubrics.
    if "min_score" in exp:
        score_key = (
            "trajectory_quality_score" if kind == "trajectory" else "answer_quality_score"
        )
        got = float(result.get(score_key) or 0.0)
        assert got >= float(exp["min_score"]), (
            f"{score_key} {got} below min_score {exp['min_score']}"
        )


class _SkipCase(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or CASE_BANK_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases") or []
    if not cases:
        raise ValueError(f"no cases in {p}")
    return cases


def run_all(path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run all cases; return (results, summary_with_axes)."""
    cases = load_cases(path)
    results: list[dict[str, Any]] = []

    for case in cases:
        cid = case["id"]
        axis = case.get("axis", "direction_accuracy")

        if case.get("disabled"):
            results.append({"id": cid, "ok": True, "skipped": True, "reason": "disabled", "axis": axis})
            continue

        try:
            _execute_check(case)
            results.append({"id": cid, "ok": True, "axis": axis})
        except _SkipCase as sk:
            results.append({"id": cid, "ok": True, "skipped": True, "reason": sk.reason, "axis": axis})
        except AssertionError as e:
            results.append({"id": cid, "ok": False, "error": str(e), "axis": axis})
        except Exception as e:
            results.append({"id": cid, "ok": False, "error": f"{type(e).__name__}: {e}", "axis": axis})

    axes_template: dict[str, dict[str, int]] = {
        "direction_accuracy": {"passed": 0, "failed": 0, "skipped": 0, "total": 0},
        "json_validity": {"passed": 0, "failed": 0, "skipped": 0, "total": 0},
        "shortcut_resistance": {"passed": 0, "failed": 0, "skipped": 0, "total": 0},
        "reasoning_quality": {"passed": 0, "failed": 0, "skipped": 0, "total": 0},
    }

    for case, res in zip(cases, results):
        axis = res.get("axis") or "direction_accuracy"
        if axis not in axes_template:
            axis = "direction_accuracy"
        st = axes_template[axis]
        st["total"] += 1
        if res.get("skipped"):
            st["skipped"] += 1
        elif res.get("ok"):
            st["passed"] += 1
        else:
            st["failed"] += 1

    failed = [r for r in results if not r.get("ok") and not r.get("skipped")]
    summary = {
        "total_cases": len(cases),
        "passed": sum(1 for r in results if r.get("ok") and not r.get("skipped")),
        "failed_count": len(failed),
        "axes": axes_template,
        "failures": failed,
        "reasoning_quality_note": (
            "Set TEVV_LLM_JUDGE=1 for future LLM-as-judge cases (stub in v1)."
        ),
    }
    return results, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TEVV chat eval cases")
    parser.add_argument("--json", action="store_true", help="print JSON summary to stdout")
    parser.add_argument("--case-bank", type=Path, default=None, help="override case_bank.json path")
    args = parser.parse_args()

    _results, summary = run_all(path=args.case_bank)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("TEVV chat harness (deterministic)")
        print(f"  passed: {summary['passed']}/{summary['total_cases']}")
        print(f"  failed: {summary['failed_count']}")
        for ax, stats in summary["axes"].items():
            print(f"  axis {ax}: {stats}")
        if summary["failures"]:
            print("Failures:")
            for f in summary["failures"]:
                print(f"  - {f['id']}: {f.get('error', '')}")

    return 0 if summary["failed_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
