from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .benchmarks import load_benchmark_config, median_benchmark_ms
from .reports import write_ubds_reports
from .schemas import TaskResultRow, UbdsRunResult
from .scoring import (
    AccessibilityInput,
    SatisfactionInput,
    VisualDesignInput,
    calculate_ubds,
    grade_from_score,
    release_gate,
    score_accessibility,
    score_efficiency,
    score_errors,
    score_navigation,
    score_satisfaction,
    score_task_success,
    score_visual,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASETS_DIR = REPO_ROOT / "evals" / "datasets"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_task_fixture(path: Path) -> list[TaskResultRow]:
    if not path.is_file():
        return []
    rows: list[TaskResultRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        rows.append(TaskResultRow.model_validate(obj))
    return rows


def _load_playwright_results(path: Path) -> list[TaskResultRow]:
    data = _load_json(path)
    tasks = data.get("tasks") or []
    rows: list[TaskResultRow] = []
    for t in tasks:
        rows.append(
            TaskResultRow(
                task_id=str(t.get("task_id", "")),
                task_name=str(t.get("task_name", "")),
                completed=bool(t.get("completed", False)),
                time_on_task_ms=float(t.get("time_on_task_ms", 0)),
                error_count=int(t.get("error_count", 0)),
                steps=int(t.get("steps", 0)),
                backtrack_count=int(t.get("backtrack_count", 0)),
                seq_score=t.get("seq_score"),
                critical=bool(t.get("critical", True)),
            )
        )
    return rows


def _resolve_audit_path(
    repo_root: Path,
    *,
    latest_name: str,
    fixture_name: str,
    mode: str,
) -> Path:
    latest = repo_root / "evals" / "reports" / latest_name
    fixture = repo_root / "evals" / "datasets" / fixture_name
    if mode == "playwright" and latest.is_file():
        return latest
    return fixture if fixture.is_file() else latest


def _load_accessibility(path: Path) -> AccessibilityInput:
    data = _load_json(path)
    if not data:
        return AccessibilityInput()
    return AccessibilityInput(
        critical_issues=int(data.get("critical_accessibility_issues", 0)),
        moderate_issues=int(data.get("moderate_accessibility_issues", 0)),
        contrast_pass_rate=float(data.get("contrast_pass_rate", 1.0)),
        mobile_layout_pass_rate=float(data.get("mobile_layout_pass_rate", 1.0)),
        keyboard_navigation_success=float(data.get("keyboard_navigation_success", 1.0)),
    )


def _load_visual(path: Path) -> VisualDesignInput:
    data = _load_json(path)
    if not data:
        return VisualDesignInput()
    return VisualDesignInput(
        design_system_compliance_rate=float(data.get("design_system_compliance_rate", 0.9)),
        contrast_pass_rate=float(data.get("contrast_pass_rate", 0.95)),
        empty_state_coverage=float(data.get("empty_state_coverage", 0.85)),
        loading_state_coverage=float(data.get("loading_state_coverage", 0.85)),
        error_state_coverage=float(data.get("error_state_coverage", 0.85)),
    )


def _load_satisfaction(path: Path) -> SatisfactionInput:
    data = _load_json(path)
    if not data:
        return SatisfactionInput()
    return SatisfactionInput(
        sus_score=float(data.get("sus_score", 75.0)),
        csat_score=float(data.get("csat_score", 0.85)),
        seq_score=float(data.get("seq_score", 6.0)),
        trust_rating=float(data.get("trust_rating", 0.82)),
        nps=float(data.get("nps", 25.0)),
    )


def _infer_strengths_issues(
    categories: dict[str, Any],
    tasks: list[TaskResultRow],
) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    issues: list[str] = []
    for key, cat in categories.items():
        if cat.score >= 85:
            strengths.append(f"Strong {key.replace('_', ' ')} ({cat.score})")
        elif cat.score < 75:
            issues.append(f"Needs improvement: {key.replace('_', ' ')} ({cat.score})")
    slow = [t for t in tasks if t.completed and t.time_on_task_ms > 90000]
    if slow:
        issues.append(f"{len(slow)} task(s) exceeded 90s median benchmark")
    failed = [t for t in tasks if not t.completed]
    if failed:
        issues.append(f"{len(failed)} task(s) did not complete")
    if not strengths:
        strengths.append("Baseline UBDS run completed")
    return strengths[:5], issues[:5]


def run_ubds(
    *,
    mode: str = "fixture",
    app_version: str | None = None,
    benchmark_type: str = "internal",
    playwright_path: Path | None = None,
    repo_root: Path | None = None,
) -> UbdsRunResult:
    root = repo_root or REPO_ROOT
    missing: list[str] = []
    now = datetime.now(timezone.utc)
    run_id = f"uiux_eval_{now.strftime('%Y%m%d_%H%M%S')}"

    datasets_dir = root / "evals" / "datasets"
    fixture_path = datasets_dir / "ubds_task_results.jsonl"
    a11y_path = _resolve_audit_path(
        root,
        latest_name="ubds_accessibility_latest.json",
        fixture_name="ubds_accessibility_fixture.json",
        mode=mode,
    )
    visual_path = _resolve_audit_path(
        root,
        latest_name="ubds_visual_latest.json",
        fixture_name="ubds_visual_fixture.json",
        mode=mode,
    )
    survey_path = datasets_dir / "ubds_satisfaction_fixture.json"

    bench_cfg = load_benchmark_config(root)
    bench_median = median_benchmark_ms(bench_cfg)

    tasks: list[TaskResultRow] = []
    if mode == "playwright":
        pw = playwright_path or root / "evals" / "reports" / "ubds_playwright_latest.json"
        tasks = _load_playwright_results(pw)
        if not tasks:
            missing.append(str(pw))
            tasks = _load_task_fixture(fixture_path)
    else:
        tasks = _load_task_fixture(fixture_path)
        if not tasks:
            missing.append(str(fixture_path))

    if not a11y_path.is_file():
        missing.append(str(a11y_path))
    if not visual_path.is_file():
        missing.append(str(visual_path))

    categories = {
        "task_success_completion": score_task_success(tasks),
        "efficiency_flow_friction": score_efficiency(tasks, benchmark_median_ms=bench_median),
        "error_rate_recovery": score_errors(tasks),
        "navigation_information_architecture": score_navigation(tasks),
        "visual_design_consistency": score_visual(_load_visual(visual_path)),
        "accessibility_responsiveness": score_accessibility(_load_accessibility(a11y_path)),
        "user_satisfaction_trust": score_satisfaction(_load_satisfaction(survey_path), tasks),
    }
    overall = calculate_ubds(categories)
    grade = grade_from_score(overall)

    critical = [t for t in tasks if t.critical]
    crit_abandon = 1.0 - (
        sum(1 for t in critical if t.completed) / len(critical) if critical else 1.0
    )
    a11y = _load_accessibility(a11y_path)
    status = release_gate(
        overall,
        categories,
        critical_issues=a11y.critical_issues,
        critical_abandon_rate=crit_abandon,
    )

    strengths, issues = _infer_strengths_issues(categories, tasks)
    rec = (
        "Release can proceed with monitored follow-ups."
        if status == "pass"
        else "Address top UBDS issues before broad release."
        if status == "hold"
        else "Do not release until critical tasks and accessibility blockers are resolved."
    )

    version = app_version or os.environ.get("APP_VERSION", "dev")
    result = UbdsRunResult(
        run_id=run_id,
        timestamp=now.isoformat(),
        app_name="TradeTalk",
        version=version,
        benchmark_type=benchmark_type,
        overall_ui_behavior_design_score=round(overall, 2),
        grade=grade,
        status=status,
        scores=categories,
        task_results=tasks,
        top_strengths=strengths,
        top_issues=issues,
        recommendation=rec,
        missing_inputs=missing,
    )
    write_ubds_reports(result, root)
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="UBDS v1.0 UI/UX benchmark runner")
    parser.add_argument("--mode", choices=["fixture", "playwright"], default="fixture")
    parser.add_argument("--version", default=None)
    parser.add_argument("--playwright-json", default=None)
    args = parser.parse_args()
    pw = Path(args.playwright_json) if args.playwright_json else None
    result = run_ubds(mode=args.mode, app_version=args.version, playwright_path=pw)
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "overall": result.overall_ui_behavior_design_score,
                "grade": result.grade,
                "status": result.status,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
