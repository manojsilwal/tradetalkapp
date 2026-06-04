from __future__ import annotations

import argparse
import json
from datetime import timezone
from pathlib import Path
from typing import Any

import yaml

from .reports import make_run_id, utc_now, write_all_outputs
from .scoring import (
    aggregate_safety,
    build_ablation_row,
    build_dashboard_summary,
    calculate_aes,
    calculate_swarm_advantage,
    choose_release_decision,
    complexity_tax_breakdown,
    dashboard_status_from_gates,
    round_score,
    to_variant_score,
)
from .schemas import CategoryScores, EvaluationRunResult, SafetyMetrics, VariantMetrics


REQUIRED_DATASETS = [
    "golden_tasks.jsonl",
    "rag_grounding_cases.jsonl",
    "agent_orchestration_cases.jsonl",
    "hallucination_adversarial.jsonl",
    "memory_regression_cases.jsonl",
    "tool_failure_cases.jsonl",
]

REQUIRED_CONFIGS = [
    "production_swarm.yaml",
    "single_agent_rag.yaml",
    "planner_executor.yaml",
    "reduced_swarm.yaml",
    "no_critic.yaml",
    "no_reflection.yaml",
    "no_rrf_memory.yaml",
    "no_mutation_engine.yaml",
    "no_coral_meta_llm.yaml",
]

VARIANT_NAME_MAP = {
    "production_swarm": "Production 5-Agent Swarm",
    "single_agent_rag": "Single-Agent RAG",
    "planner_executor": "Planner-Executor",
    "reduced_swarm": "Reduced Swarm + Current LLM",
    "no_critic": "Production swarm without critic",
    "no_reflection": "Production swarm without reflection loop",
    "no_rrf_memory": "Production swarm without RRF memory retrieval",
    "no_mutation_engine": "Production swarm without Nightly Mutation Engine",
    "no_coral_meta_llm": "Production swarm without CORAL / Meta-LLM",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _build_category_from_row(row: dict[str, Any]) -> CategoryScores:
    return CategoryScores(
        task_success=float(row.get("task_success", 0.0)),
        rag_quality=float(row.get("rag_quality", 0.0)),
        orchestration_effectiveness=float(row.get("orchestration_effectiveness", 0.0)),
        continual_learning_value=float(row.get("continual_learning_value", 0.0)),
        efficiency=float(row.get("efficiency", 0.0)),
        safety_groundedness=float(row.get("safety_groundedness", 0.0)),
        maintainability=float(row.get("maintainability", 0.0)),
    )


def _build_safety_from_row(row: dict[str, Any]) -> SafetyMetrics:
    return SafetyMetrics(
        hallucination_rate=float(row.get("hallucination_rate", 0.0)),
        critical_hallucinations=int(row.get("critical_hallucinations", 0)),
        fabricated_tool_call_claims=int(row.get("fabricated_tool_call_claims", 0)),
        tool_call_validity=float(row.get("tool_call_validity", 1.0)),
        citation_validity=float(row.get("citation_validity", 1.0)),
    )


def _merge_rows(rows: list[dict[str, Any]], variant: str) -> VariantMetrics | None:
    scoped = [r for r in rows if str(r.get("variant", "")).strip() == variant]
    if not scoped:
        return None
    return VariantMetrics(
        name=VARIANT_NAME_MAP.get(variant, variant),
        p95_latency_ms=_avg([float(r.get("p95_latency_ms", 0.0)) for r in scoped]),
        cost_per_task=_avg([float(r.get("cost_per_task", 0.0)) for r in scoped]),
        category_scores=CategoryScores(
            task_success=_avg([float(r.get("task_success", 0.0)) for r in scoped]),
            rag_quality=_avg([float(r.get("rag_quality", 0.0)) for r in scoped]),
            orchestration_effectiveness=_avg([float(r.get("orchestration_effectiveness", 0.0)) for r in scoped]),
            continual_learning_value=_avg([float(r.get("continual_learning_value", 0.0)) for r in scoped]),
            efficiency=_avg([float(r.get("efficiency", 0.0)) for r in scoped]),
            safety_groundedness=_avg([float(r.get("safety_groundedness", 0.0)) for r in scoped]),
            maintainability=_avg([float(r.get("maintainability", 0.0)) for r in scoped]),
        ),
        safety=SafetyMetrics(
            hallucination_rate=_avg([float(r.get("hallucination_rate", 0.0)) for r in scoped]),
            critical_hallucinations=max(int(r.get("critical_hallucinations", 0)) for r in scoped),
            fabricated_tool_call_claims=max(int(r.get("fabricated_tool_call_claims", 0)) for r in scoped),
            tool_call_validity=min(float(r.get("tool_call_validity", 1.0)) for r in scoped),
            citation_validity=min(float(r.get("citation_validity", 1.0)) for r in scoped),
        ),
    )


def _load_configs(configs_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cfg_name in REQUIRED_CONFIGS:
        path = configs_dir / cfg_name
        if not path.exists():
            continue
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(parsed, dict):
            out[path.stem] = parsed
    return out


def _load_evaluation_datasets(
    datasets_dir: Path,
    mode: str,
    missing_inputs: list[str],
    skipped_tests: list[dict[str, str]],
) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    for name in REQUIRED_DATASETS:
        path = datasets_dir / name
        if not path.exists():
            missing_inputs.append(f"Missing dataset: /evals/datasets/{name}")
            skipped_tests.append({"name": name, "reason": "Dataset not found"})
            continue
        if mode == "fixture":
            all_rows.extend(_load_jsonl(path))
    return all_rows


def _evaluate_variants(
    configs: dict[str, dict[str, Any]],
    mode: str,
    all_rows: list[dict[str, Any]],
    skipped_tests: list[dict[str, str]],
) -> tuple[dict[str, VariantMetrics], dict[str, float | None], list[VariantScore]]:
    variant_metrics: dict[str, VariantMetrics] = {}
    score_map: dict[str, float | None] = {}
    variant_rows = []

    for key, pretty in VARIANT_NAME_MAP.items():
        if key not in configs:
            metrics = VariantMetrics(name=pretty, status="skipped", status_reason="Variant config unavailable")
            variant_metrics[key] = metrics
            score_map[key] = None
            variant_rows.append(to_variant_score(metrics, None, decision="Skipped"))
            continue
        if mode == "dry-run":
            metrics = VariantMetrics(name=pretty, status="skipped", status_reason="dry-run mode")
            variant_metrics[key] = metrics
            score_map[key] = None
            variant_rows.append(to_variant_score(metrics, None, decision="Skipped"))
            continue

        merged = _merge_rows(all_rows, key)
        if merged is None:
            metrics = VariantMetrics(name=pretty, status="skipped", status_reason="No fixture metrics found")
            skipped_tests.append({"name": key, "reason": "No fixture metrics found"})
            variant_metrics[key] = metrics
            score_map[key] = None
            variant_rows.append(to_variant_score(metrics, None, decision="Skipped"))
            continue
        variant_metrics[key] = merged
        aes = calculate_aes(merged.category_scores)
        score_map[key] = aes
        variant_rows.append(to_variant_score(merged, aes))

    return variant_metrics, score_map, variant_rows


def _evaluate_winner(
    score_map: dict[str, float | None],
) -> tuple[str, float | None]:
    production_aes = score_map.get("production_swarm")
    winner_key = "production_swarm"
    winner_aes = production_aes
    for key, aes in score_map.items():
        if aes is None:
            continue
        if winner_aes is None or aes > winner_aes:
            winner_key = key
            winner_aes = aes
    return winner_key, winner_aes


def _generate_recommendations(
    candidate_beats: bool,
    winner_key: str,
    ablations: list[ComponentAblationResult],
) -> list[str]:
    recommendations: list[str] = []
    if candidate_beats:
        recommendations.append(f"Shadow deploy {VARIANT_NAME_MAP.get(winner_key, winner_key)} and monitor real traffic.")
    else:
        recommendations.append("Keep production swarm as baseline until a candidate clearly wins.")
    for row in ablations:
        if row.recommendation in {"Remove or redesign", "Disable by default", "Make conditional"}:
            recommendations.append(f"{row.component}: {row.recommendation}.")
    if not recommendations:
        recommendations.append("No immediate architecture changes recommended.")
    return recommendations


def run_swarm_score(
    *,
    repo_root: Path,
    mode: str = "fixture",
    write_outputs: bool = True,
) -> dict[str, Any]:
    now = utc_now()
    timestamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_id = make_run_id(now)

    datasets_dir = repo_root / "evals" / "datasets"
    configs_dir = repo_root / "evals" / "configs"
    missing_inputs: list[str] = []
    skipped_tests: list[dict[str, str]] = []

    all_rows = _load_evaluation_datasets(
        datasets_dir=datasets_dir,
        mode=mode,
        missing_inputs=missing_inputs,
        skipped_tests=skipped_tests,
    )

    configs = _load_configs(configs_dir)
    for name in REQUIRED_CONFIGS:
        if name.replace(".yaml", "") not in configs:
            missing_inputs.append(f"Missing config: /evals/configs/{name}")

    variant_metrics, score_map, variant_rows = _evaluate_variants(
        configs=configs,
        mode=mode,
        all_rows=all_rows,
        skipped_tests=skipped_tests,
    )

    production_aes = score_map.get("production_swarm")
    winner_key, winner_aes = _evaluate_winner(score_map)

    candidate_beats = (
        winner_key != "production_swarm"
        and winner_aes is not None
        and production_aes is not None
        and winner_aes > production_aes
    )
    risky_component_changed = winner_key in {"no_mutation_engine", "no_coral_meta_llm"}

    winner_metrics = variant_metrics[winner_key]
    production_metrics = variant_metrics["production_swarm"]
    aggregate = aggregate_safety(variant_metrics.values())
    status = dashboard_status_from_gates(
        critical_hallucinations=winner_metrics.safety.critical_hallucinations,
        fabricated_tool_call_claims=winner_metrics.safety.fabricated_tool_call_claims,
        tool_call_validity=winner_metrics.safety.tool_call_validity,
        citation_validity=winner_metrics.safety.citation_validity,
        risky_component_changed=risky_component_changed,
        candidate_beats_production=candidate_beats,
    )
    decision = choose_release_decision(
        production_aes=production_aes,
        winner_aes=winner_aes,
        production_cost=production_metrics.cost_per_task if production_aes is not None else None,
        winner_cost=winner_metrics.cost_per_task if winner_aes is not None else None,
        production_p95_ms=production_metrics.p95_latency_ms if production_aes is not None else None,
        winner_p95_ms=winner_metrics.p95_latency_ms if winner_aes is not None else None,
        status=status,
    )

    swarm_adv = calculate_swarm_advantage(
        production_aes,
        [score_map.get("single_agent_rag"), score_map.get("planner_executor"), score_map.get("reduced_swarm")],
    )
    complexity = complexity_tax_breakdown(production_metrics, winner_metrics)

    ablation_specs = [
        ("Critic agent", "no_critic"),
        ("Reflection loop", "no_reflection"),
        ("RRF memory retrieval", "no_rrf_memory"),
        ("Nightly Mutation Engine", "no_mutation_engine"),
        ("CORAL / Meta-LLM", "no_coral_meta_llm"),
    ]
    ablations = []
    for label, key in ablation_specs:
        without = score_map.get(key)
        if key not in configs:
            ablations.append(
                build_ablation_row(
                    label,
                    production_aes,
                    None,
                    status="skipped",
                    reason="Variant config unavailable",
                )
            )
        else:
            ablations.append(build_ablation_row(label, production_aes, without))

    recommendations = _generate_recommendations(
        candidate_beats=candidate_beats,
        winner_key=winner_key,
        ablations=ablations,
    )

    report_rel_path = f"/evals/reports/{timestamp[:10]}/weekly_swarm_effectiveness_report.md"
    dashboard_summary = build_dashboard_summary(
        run_id=run_id,
        timestamp=timestamp,
        status=status,
        winner_key=winner_key,
        production_key="production_swarm",
        score_map=score_map,
        metric_map=variant_metrics,
        swarm_advantage_score=swarm_adv,
        complexity_tax=complexity,
        recommendation=recommendations[0],
        report_path=report_rel_path,
        top_actions=recommendations[:5],
    )

    scores_payload: dict[str, dict[str, Any]] = {}
    for key, metrics in variant_metrics.items():
        aes = score_map.get(key)
        if aes is None:
            continue
        scores_payload[key] = {
            "aes": round_score(aes),
            "task_success": round_score(metrics.category_scores.task_success),
            "rag_quality": round_score(metrics.category_scores.rag_quality),
            "orchestration": round_score(metrics.category_scores.orchestration_effectiveness),
            "continual_learning": round_score(metrics.category_scores.continual_learning_value),
            "efficiency": round_score(metrics.category_scores.efficiency),
            "safety_groundedness": round_score(metrics.category_scores.safety_groundedness),
            "maintainability": round_score(metrics.category_scores.maintainability),
        }

    result = EvaluationRunResult(
        run_id=run_id,
        timestamp=timestamp,
        production_version=str(configs.get("production_swarm", {}).get("version", "v0.0.0")),
        benchmark_suite="agentic_swarm_eval_v1",
        decision=decision,
        winner=VARIANT_NAME_MAP.get(winner_key, winner_key),
        scores=scores_payload,
        swarm_advantage_score=swarm_adv,
        complexity_tax=complexity.level,
        ablation_results=ablations,
        safety=aggregate,
        recommendations=recommendations,
        dashboard_notification=dashboard_summary.model_dump(),
        missing_inputs=missing_inputs,
        skipped_tests=skipped_tests,
        variant_scores=variant_rows,
    )

    files_written = {}
    if write_outputs:
        files_written = {
            k: str(v)
            for k, v in write_all_outputs(
                repo_root=repo_root,
                run_result=result,
                variant_rows=variant_rows,
                ablation_rows=ablations,
                dashboard_summary=dashboard_summary,
                complexity_tax_detail={
                    "latency_tax": complexity.latency_tax,
                    "cost_tax": complexity.cost_tax,
                    "failure_tax": complexity.failure_tax,
                    "maintenance_tax": complexity.maintenance_tax,
                },
                regression_failures=[
                    {
                        "name": item.get("name"),
                        "reason": item.get("reason"),
                        "run_id": run_id,
                    }
                    for item in skipped_tests
                ],
            ).items()
        }

    return {
        "run_id": run_id,
        "decision": decision,
        "winner": result.winner,
        "production_aes": score_map.get("production_swarm"),
        "winning_aes": winner_aes,
        "delta": round_score((winner_aes - production_aes)) if (winner_aes is not None and production_aes is not None) else None,
        "swarm_advantage": swarm_adv,
        "complexity_tax": complexity.level,
        "files": files_written,
        "result": result.model_dump(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SwarmScore evaluator")
    parser.add_argument("--mode", choices=["fixture", "dry-run"], default="fixture")
    parser.add_argument("--no-write", action="store_true", help="Do not write output files")
    parser.add_argument("--repo-root", default=".", help="Path to repository root")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    output = run_swarm_score(repo_root=repo_root, mode=args.mode, write_outputs=not args.no_write)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

