from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .schemas import UbdsRunResult


def _category_scores_dict(result: UbdsRunResult) -> dict[str, float]:
    return {k: float(v.score) for k, v in result.scores.items()}


def _previous_overall_score(repo_root: Path) -> float | None:
    hist_path = repo_root / "frontend" / "public" / "dashboard" / "uiux-history.json"
    if not hist_path.is_file():
        return None
    try:
        history = json.loads(hist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(history, list) or len(history) < 2:
        return None
    prev = history[-2]
    score = prev.get("overall_score")
    return float(score) if score is not None else None


def build_results_json(result: UbdsRunResult) -> dict[str, Any]:
    payload = {
        "run_id": result.run_id,
        "app_name": result.app_name,
        "version": result.version,
        "benchmark_type": result.benchmark_type,
        "overall_ui_behavior_design_score": result.overall_ui_behavior_design_score,
        "grade": result.grade,
        "status": result.status,
        "scores": {
            k: {
                "score": v.score,
                "weight": v.weight,
                "metrics": v.metrics,
            }
            for k, v in result.scores.items()
        },
        "task_results": [t.model_dump() for t in result.task_results],
        "top_strengths": result.top_strengths,
        "top_issues": result.top_issues,
        "recommendation": result.recommendation,
        "missing_inputs": result.missing_inputs,
        "timestamp": result.timestamp,
        "previous_overall_score": result.previous_overall_score,
        "overall_score_delta": result.overall_score_delta,
    }
    return payload


def build_dashboard_summary(result: UbdsRunResult, report_rel: str) -> dict[str, Any]:
    cats = _category_scores_dict(result)
    dash = {
        "run_id": result.run_id,
        "timestamp": result.timestamp,
        "app_name": result.app_name,
        "version": result.version,
        "status": result.status,
        "overall_score": result.overall_ui_behavior_design_score,
        "grade": result.grade,
        "top_strengths": result.top_strengths,
        "top_issues": result.top_issues,
        "category_scores": cats,
        "recommendation": result.recommendation,
        "report_path": report_rel,
        "previous_overall_score": result.previous_overall_score,
        "overall_score_delta": result.overall_score_delta,
    }
    return dash


def render_markdown_report(result: UbdsRunResult) -> str:
    cats = result.scores
    lines = [
        "# UI Behavior & Design Benchmark Report",
        "",
        f"Date: {result.timestamp[:10]}",
        f"Run ID: {result.run_id}",
        f"App: {result.app_name}",
        f"Version: {result.version}",
        f"Benchmark Type: {result.benchmark_type}",
        "",
        "## 1. Executive Summary",
        "",
        f"Overall UBDS Score: **{result.overall_ui_behavior_design_score}**",
        f"Grade: **{result.grade}**",
        f"Status: **{result.status.upper()}**",
        "",
        result.recommendation,
        "",
        "## 2. Category Scores",
        "",
        "| Category | Weight | Score |",
        "|---|---:|---:|",
    ]
    labels = {
        "task_success_completion": "Task Success & Completion",
        "efficiency_flow_friction": "Efficiency & Flow Friction",
        "error_rate_recovery": "Error Rate & Recovery",
        "navigation_information_architecture": "Navigation & IA",
        "visual_design_consistency": "Visual Design & Consistency",
        "accessibility_responsiveness": "Accessibility & Responsiveness",
        "user_satisfaction_trust": "User Satisfaction & Trust",
    }
    for key, cat in cats.items():
        label = labels.get(key, key)
        lines.append(f"| {label} | {int(cat.weight * 100)}% | {cat.score} |")
    lines.extend(["", "## 3. Top Task Results", "", "| Task | Completion | Time (ms) | Errors |", "|---|---:|---:|---:|"])
    for t in result.task_results:
        lines.append(
            f"| {t.task_name or t.task_id} | {'yes' if t.completed else 'no'} | {int(t.time_on_task_ms)} | {t.error_count} |"
        )
    lines.extend(["", "## 4. Strengths", ""])
    for s in result.top_strengths:
        lines.append(f"- {s}")
    lines.extend(["", "## 5. Issues", ""])
    for i in result.top_issues:
        lines.append(f"- {i}")
    lines.extend(
        [
            "",
            "## 6. Release Gate",
            "",
            f"Status: **{result.status.upper()}**",
            "",
            result.recommendation,
            "",
            "## 7. Recommendations",
            "",
            "1. Re-run Playwright UBDS tasks after navigation or agent UI changes.",
            "2. Track SEQ and trust on AI-source inspection flows.",
            "3. Keep critical accessibility issues at zero before release.",
            "",
            "## 8. Missing Data / Limitations",
            "",
        ]
    )
    if result.missing_inputs:
        for m in result.missing_inputs:
            lines.append(f"- {m}")
    else:
        lines.append("- None for this run.")
    lines.extend(["", "## 9. Benchmark Comparison", ""])
    if result.previous_overall_score is not None and result.overall_score_delta is not None:
        lines.append(f"Previous Score: **{result.previous_overall_score}**")
        lines.append(f"Current Score: **{result.overall_ui_behavior_design_score}**")
        sign = "+" if result.overall_score_delta >= 0 else ""
        lines.append(f"Delta: **{sign}{result.overall_score_delta}**")
    else:
        lines.append("No prior run in history — baseline established.")
    return "\n".join(lines) + "\n"


def write_ubds_reports(result: UbdsRunResult, repo_root: Path) -> dict[str, str]:
    prev = _previous_overall_score(repo_root)
    if prev is not None:
        result.previous_overall_score = prev
        result.overall_score_delta = round(
            float(result.overall_ui_behavior_design_score) - prev, 2
        )

    date_str = result.timestamp[:10]
    day_dir = repo_root / "evals" / "reports" / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    md_path = day_dir / "uiux_behavior_design_report.md"
    json_path = day_dir / "uiux_behavior_design_results.json"
    csv_path = day_dir / "uiux_task_results.csv"
    a11y_path = day_dir / "uiux_accessibility_results.json"
    dash_path = day_dir / "uiux_dashboard_summary.json"

    md_path.write_text(render_markdown_report(result), encoding="utf-8")
    json_path.write_text(json.dumps(build_results_json(result), indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "task_id",
                "task_name",
                "completed",
                "time_on_task_ms",
                "error_count",
                "steps",
                "backtrack_count",
                "seq_score",
                "critical",
            ],
        )
        writer.writeheader()
        for t in result.task_results:
            writer.writerow(t.model_dump())

    a11y = result.scores.get("accessibility_responsiveness")
    a11y_payload = a11y.metrics if a11y else {}
    a11y_path.write_text(json.dumps(a11y_payload, indent=2), encoding="utf-8")

    report_rel = f"/evals/reports/{date_str}/uiux_behavior_design_report.md"
    dash = build_dashboard_summary(result, report_rel)
    dash_path.write_text(json.dumps(dash, indent=2), encoding="utf-8")

    reports_root = repo_root / "evals" / "reports"
    (reports_root / "latest_uiux.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    (reports_root / "latest_uiux_results.json").write_text(
        json_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    pub = repo_root / "frontend" / "public" / "dashboard"
    pub.mkdir(parents=True, exist_ok=True)
    (pub / "uiux-summary.json").write_text(json.dumps(dash, indent=2), encoding="utf-8")

    history_path = pub / "uiux-history.json"
    history: list[dict[str, Any]] = []
    if history_path.is_file():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []
    history.append(
        {
            "run_id": result.run_id,
            "timestamp": result.timestamp,
            "overall_score": result.overall_ui_behavior_design_score,
            "grade": result.grade,
            "status": result.status,
        }
    )
    history = history[-30:]
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "csv": str(csv_path),
        "dashboard": str(dash_path),
    }


def read_latest_summary(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "frontend" / "public" / "dashboard" / "uiux-summary.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_latest_results(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "evals" / "reports" / "latest_uiux_results.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
