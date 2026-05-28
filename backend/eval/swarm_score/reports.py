from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import ComponentAblationResult, DashboardSummary, EvaluationRunResult, VariantScore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_run_id(now: datetime | None = None) -> str:
    ts = now or utc_now()
    return f"eval_{ts.strftime('%Y%m%d_%H%M%S')}"


def date_dir_name(now: datetime | None = None) -> str:
    ts = now or utc_now()
    return ts.strftime("%Y-%m-%d")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_variant_scores_csv(path: Path, rows: list[VariantScore]) -> None:
    ensure_dir(path.parent)
    fields = [
        "variant",
        "status",
        "status_reason",
        "aes",
        "task_success",
        "rag_quality",
        "orchestration",
        "learning",
        "efficiency",
        "safety",
        "maintainability",
        "p95_latency_ms",
        "cost_per_task",
        "decision",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "variant": row.name,
                    "status": row.status,
                    "status_reason": row.status_reason or "",
                    "aes": row.aes if row.aes is not None else "",
                    "task_success": row.task_success if row.task_success is not None else "",
                    "rag_quality": row.rag_quality if row.rag_quality is not None else "",
                    "orchestration": row.orchestration if row.orchestration is not None else "",
                    "learning": row.learning if row.learning is not None else "",
                    "efficiency": row.efficiency if row.efficiency is not None else "",
                    "safety": row.safety if row.safety is not None else "",
                    "maintainability": row.maintainability if row.maintainability is not None else "",
                    "p95_latency_ms": row.p95_latency_ms if row.p95_latency_ms is not None else "",
                    "cost_per_task": row.cost_per_task if row.cost_per_task is not None else "",
                    "decision": row.decision,
                }
            )


def write_ablation_csv(path: Path, rows: list[ComponentAblationResult]) -> None:
    ensure_dir(path.parent)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["component", "status", "reason", "with_score", "without_score", "delta", "recommendation"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "component": row.component,
                    "status": row.status,
                    "reason": row.reason or "",
                    "with_score": row.with_score if row.with_score is not None else "",
                    "without_score": row.without_score if row.without_score is not None else "",
                    "delta": row.delta if row.delta is not None else "",
                    "recommendation": row.recommendation,
                }
            )


def format_markdown_report(
    *,
    run_result: EvaluationRunResult,
    variant_rows: list[VariantScore],
    ablation_rows: list[ComponentAblationResult],
    report_date: str,
    complexity_tax_detail: dict[str, float],
) -> str:
    lines: list[str] = []
    lines.append("# Weekly Swarm Effectiveness Report")
    lines.append("")
    lines.append(f"Date: {report_date}")
    lines.append(f"Run ID: {run_result.run_id}")
    lines.append(f"Production Version: {run_result.production_version}")
    lines.append(f"Benchmark Suite: {run_result.benchmark_suite}")
    lines.append("")
    lines.append("## 1. Executive Decision")
    lines.append("")
    lines.append(f"Winner: {run_result.winner}")
    lines.append(f"Decision: {run_result.decision}")
    lines.append("")
    lines.append("Summary:")
    summary = run_result.dashboard_notification.get("recommendation") or "No recommendation generated."
    lines.append(summary)
    lines.append("")
    lines.append("## 2. Score Summary")
    lines.append("")
    lines.append("| Variant | AES | Task Success | RAG Quality | Orchestration | Learning | Efficiency | Safety | Maintainability | p95 Latency | Cost | Decision |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in variant_rows:
        lines.append(
            "| {name} | {aes} | {task} | {rag} | {orch} | {learning} | {eff} | {safety} | {maint} | {p95} | {cost} | {decision} |".format(
                name=row.name,
                aes=row.aes if row.aes is not None else "skipped",
                task=row.task_success if row.task_success is not None else "-",
                rag=row.rag_quality if row.rag_quality is not None else "-",
                orch=row.orchestration if row.orchestration is not None else "-",
                learning=row.learning if row.learning is not None else "-",
                eff=row.efficiency if row.efficiency is not None else "-",
                safety=row.safety if row.safety is not None else "-",
                maint=row.maintainability if row.maintainability is not None else "-",
                p95=row.p95_latency_ms if row.p95_latency_ms is not None else "-",
                cost=row.cost_per_task if row.cost_per_task is not None else "-",
                decision=row.decision or row.status,
            )
        )
    lines.append("")
    lines.append("## 3. Swarm Advantage")
    lines.append("")
    prod = run_result.scores.get("production_swarm", {})
    lines.append(f"Production Swarm Score: {prod.get('aes', 'n/a')}")
    simpler_best = "n/a"
    if run_result.swarm_advantage_score is not None and prod.get("aes") is not None:
        simpler_best = round(float(prod["aes"]) - float(run_result.swarm_advantage_score), 2)
    lines.append(f"Best Simpler Baseline Score: {simpler_best}")
    lines.append(f"Swarm Advantage Score: {run_result.swarm_advantage_score if run_result.swarm_advantage_score is not None else 'n/a'}")
    lines.append("")
    lines.append("Recommendation:")
    lines.append(run_result.dashboard_notification.get("recommendation", "No recommendation generated."))
    lines.append("")
    lines.append("## 4. Complexity Tax")
    lines.append("")
    lines.append(f"Latency Tax: {complexity_tax_detail.get('latency_tax', 0.0)}")
    lines.append(f"Cost Tax: {complexity_tax_detail.get('cost_tax', 0.0)}")
    lines.append(f"Failure Tax: {complexity_tax_detail.get('failure_tax', 0.0)}")
    lines.append(f"Maintenance Tax: {complexity_tax_detail.get('maintenance_tax', 0.0)}")
    lines.append("")
    lines.append(f"Overall Complexity Tax: {run_result.complexity_tax.capitalize()}")
    lines.append("")
    lines.append("## 5. Component Ablation Results")
    lines.append("")
    lines.append("| Component | With Score | Without Score | Delta | Recommendation |")
    lines.append("|---|---:|---:|---:|---|")
    for row in ablation_rows:
        lines.append(
            f"| {row.component} | {row.with_score if row.with_score is not None else '-'} | "
            f"{row.without_score if row.without_score is not None else '-'} | "
            f"{row.delta if row.delta is not None else '-'} | {row.recommendation} |"
        )
    lines.append("")
    lines.append("## 6. Safety and Tool-Call Findings")
    lines.append("")
    lines.append(f"Hallucination Rate: {run_result.safety.hallucination_rate}")
    lines.append(f"Critical Hallucinations: {run_result.safety.critical_hallucinations}")
    lines.append(f"Fabricated Tool-Call Claims: {run_result.safety.fabricated_tool_call_claims}")
    lines.append(f"Tool-Call Validity: {run_result.safety.tool_call_validity}")
    lines.append(f"Citation Validity: {run_result.safety.citation_validity}")
    lines.append("")
    lines.append("## 7. Dashboard Notification")
    lines.append("")
    lines.append(f"Status: {run_result.dashboard_notification.get('status', 'unknown')}")
    badge = run_result.dashboard_notification.get("dashboard_badge", {})
    lines.append(f"Dashboard Badge: {badge.get('label', 'unknown')}")
    lines.append("Summary File: /public/dashboard/eval-summary.json")
    lines.append("")
    lines.append("## 8. Missing Data / Skipped Tests")
    lines.append("")
    if run_result.missing_inputs:
        for item in run_result.missing_inputs:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    if run_result.skipped_tests:
        for item in run_result.skipped_tests:
            lines.append(f"- skipped: {item.get('name')} ({item.get('reason')})")
    lines.append("")
    lines.append("## 9. Recommended Actions")
    lines.append("")
    if run_result.recommendations:
        for idx, rec in enumerate(run_result.recommendations, start=1):
            lines.append(f"{idx}. {rec}")
    else:
        lines.append("1. No immediate actions.")
    lines.append("")
    return "\n".join(lines)


def write_all_outputs(
    *,
    repo_root: Path,
    run_result: EvaluationRunResult,
    variant_rows: list[VariantScore],
    ablation_rows: list[ComponentAblationResult],
    dashboard_summary: DashboardSummary,
    complexity_tax_detail: dict[str, float],
    regression_failures: list[dict[str, Any]],
) -> dict[str, Path]:
    date_str = run_result.timestamp[:10]
    reports_dir = repo_root / "evals" / "reports" / date_str
    ensure_dir(reports_dir)

    md_path = reports_dir / "weekly_swarm_effectiveness_report.md"
    json_path = reports_dir / "weekly_swarm_effectiveness_results.json"
    variant_csv = reports_dir / "variant_scores.csv"
    ablation_csv = reports_dir / "component_ablation_results.csv"
    regression_jsonl = reports_dir / "regression_failures.jsonl"
    dashboard_summary_run_path = reports_dir / "dashboard_summary.json"

    md_path.write_text(
        format_markdown_report(
            run_result=run_result,
            variant_rows=variant_rows,
            ablation_rows=ablation_rows,
            report_date=date_str,
            complexity_tax_detail=complexity_tax_detail,
        ),
        encoding="utf-8",
    )
    _write_json(json_path, run_result.model_dump())
    write_variant_scores_csv(variant_csv, variant_rows)
    write_ablation_csv(ablation_csv, ablation_rows)
    regression_jsonl.write_text(
        "\n".join(json.dumps(item) for item in regression_failures) + ("\n" if regression_failures else ""),
        encoding="utf-8",
    )
    _write_json(dashboard_summary_run_path, dashboard_summary.model_dump())

    latest_md = repo_root / "evals" / "reports" / "latest.md"
    latest_results = repo_root / "evals" / "reports" / "latest_results.json"
    ensure_dir(latest_md.parent)
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_results.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    public_dir = repo_root / "frontend" / "public" / "dashboard"
    ensure_dir(public_dir)
    public_summary = public_dir / "eval-summary.json"
    public_history = public_dir / "eval-history.json"
    _write_json(public_summary, dashboard_summary.model_dump())

    history: list[dict[str, Any]] = []
    if public_history.exists():
        try:
            parsed = json.loads(public_history.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                history = parsed
        except Exception:
            history = []
    history.append(dashboard_summary.model_dump())
    history = history[-100:]
    _write_json(public_history, history)

    return {
        "report_md": md_path,
        "report_json": json_path,
        "variant_csv": variant_csv,
        "ablation_csv": ablation_csv,
        "regression_jsonl": regression_jsonl,
        "dashboard_summary": public_summary,
        "dashboard_history": public_history,
    }

