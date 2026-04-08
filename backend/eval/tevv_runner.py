"""
Run TEVV chat eval cases from case_bank.json (deterministic; no live LLM required).

Three scoring axes (reported in JSON):
  - direction_accuracy — routing/intent/tool outcome alignment
  - json_validity — evidence contract shape and expected fields
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
from backend.routers.chat import (
    _extract_quote_ticker,
    _mover_query_intent,
    _wants_live_quote,
)

CASE_BANK_PATH = Path(__file__).resolve().parent / "case_bank.json"

REQUIRED_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "sources_used",
        "tools_called",
        "tool_outcomes",
        "confidence_band",
        "abstain_reason",
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

    elif check == "llm_judge":
        if os.environ.get("TEVV_LLM_JUDGE", "").strip().lower() not in ("1", "true", "yes"):
            raise _SkipCase("TEVV_LLM_JUDGE not enabled")
        raise _SkipCase("llm_judge stub — no scoring in harness v1")

    else:
        raise ValueError(f"unknown check type: {check!r}")


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
