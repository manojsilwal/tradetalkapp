from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .schemas import (
    CategoryScores,
    ComponentAblationResult,
    DashboardBadge,
    DashboardSummary,
    SafetyMetrics,
    VariantMetrics,
    VariantScore,
)


AES_WEIGHTS = {
    "task_success": 0.25,
    "rag_quality": 0.15,
    "orchestration_effectiveness": 0.20,
    "continual_learning_value": 0.10,
    "efficiency": 0.15,
    "safety_groundedness": 0.10,
    "maintainability": 0.05,
}


@dataclass
class ComplexityTaxBreakdown:
    latency_tax: float
    cost_tax: float
    failure_tax: float
    maintenance_tax: float
    total: float
    level: str


def clamp_0_100(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def round_score(value: float | None, ndigits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), ndigits)


def latency_score_from_p95(p95_latency_ms: float) -> float:
    p95_s = float(p95_latency_ms) / 1000.0
    if p95_s <= 2:
        return 100.0
    if p95_s <= 4:
        return 90.0
    if p95_s <= 6:
        return 80.0
    if p95_s <= 8:
        return 70.0
    if p95_s <= 12:
        return 55.0
    return 35.0


def calculate_aes(scores: CategoryScores) -> float:
    return clamp_0_100(
        scores.task_success * AES_WEIGHTS["task_success"]
        + scores.rag_quality * AES_WEIGHTS["rag_quality"]
        + scores.orchestration_effectiveness * AES_WEIGHTS["orchestration_effectiveness"]
        + scores.continual_learning_value * AES_WEIGHTS["continual_learning_value"]
        + scores.efficiency * AES_WEIGHTS["efficiency"]
        + scores.safety_groundedness * AES_WEIGHTS["safety_groundedness"]
        + scores.maintainability * AES_WEIGHTS["maintainability"]
    )


def calculate_swarm_advantage(
    production_aes: float | None,
    simpler_variant_scores: Iterable[float | None],
) -> float | None:
    if production_aes is None:
        return None
    valid = [float(v) for v in simpler_variant_scores if v is not None]
    if not valid:
        return None
    return round_score(float(production_aes) - max(valid))


def complexity_tax_breakdown(
    production: VariantMetrics,
    winner: VariantMetrics,
) -> ComplexityTaxBreakdown:
    # Positive values mean production is worse than winner.
    latency_penalty = max(0.0, production.p95_latency_ms - winner.p95_latency_ms)
    cost_penalty = max(0.0, production.cost_per_task - winner.cost_per_task)
    failure_penalty = max(
        0.0,
        production.safety.hallucination_rate - winner.safety.hallucination_rate,
    )
    maintenance_penalty = max(
        0.0,
        production.category_scores.maintainability - winner.category_scores.maintainability,
    )

    latency_tax = min(40.0, latency_penalty / 250.0)
    cost_tax = min(30.0, cost_penalty * 400.0)
    failure_tax = min(20.0, failure_penalty * 1500.0)
    maintenance_tax = min(10.0, maintenance_penalty * 0.5)
    total = latency_tax + cost_tax + failure_tax + maintenance_tax

    if total >= 55:
        level = "high"
    elif total >= 25:
        level = "medium"
    else:
        level = "low"
    return ComplexityTaxBreakdown(
        latency_tax=round_score(latency_tax) or 0.0,
        cost_tax=round_score(cost_tax) or 0.0,
        failure_tax=round_score(failure_tax) or 0.0,
        maintenance_tax=round_score(maintenance_tax) or 0.0,
        total=round_score(total) or 0.0,
        level=level,
    )


def dashboard_status_from_gates(
    *,
    critical_hallucinations: int,
    fabricated_tool_call_claims: int,
    tool_call_validity: float,
    citation_validity: float,
    risky_component_changed: bool,
    candidate_beats_production: bool,
) -> str:
    if (
        critical_hallucinations > 0
        or fabricated_tool_call_claims > 0
        or tool_call_validity < 0.95
        or citation_validity < 0.90
    ):
        return "fail"
    if candidate_beats_production and not risky_component_changed:
        return "shadow_recommended"
    if risky_component_changed:
        return "hold"
    return "pass"


def dashboard_badge_from_status(status: str) -> DashboardBadge:
    mapping = {
        "pass": DashboardBadge(label="Eval: Pass", color="green"),
        "hold": DashboardBadge(label="Eval: Hold", color="gray"),
        "shadow_recommended": DashboardBadge(label="Eval: Shadow Recommended", color="yellow"),
        "fail": DashboardBadge(label="Eval: Fail", color="red"),
    }
    return mapping.get(status, DashboardBadge(label="Eval: Unknown", color="gray"))


def ablation_recommendation(delta: float | None) -> str:
    if delta is None:
        return "Skipped"
    if delta >= 5:
        return "Keep always-on"
    if delta >= 2:
        return "Make conditional"
    if delta >= 0:
        return "Disable by default"
    return "Remove or redesign"


def choose_release_decision(
    *,
    production_aes: float | None,
    winner_aes: float | None,
    production_cost: float | None,
    winner_cost: float | None,
    production_p95_ms: float | None,
    winner_p95_ms: float | None,
    status: str,
) -> str:
    if status == "fail":
        return "reject"
    if status == "hold":
        return "hold"
    if production_aes is None or winner_aes is None:
        return "hold"

    aes_delta = winner_aes - production_aes
    cost_drop_ok = (
        production_cost is not None
        and winner_cost is not None
        and production_cost > 0
        and ((production_cost - winner_cost) / production_cost) >= 0.25
        and aes_delta >= -1.0
    )
    latency_drop_ok = (
        production_p95_ms is not None
        and winner_p95_ms is not None
        and production_p95_ms > 0
        and ((production_p95_ms - winner_p95_ms) / production_p95_ms) >= 0.30
    )

    if aes_delta >= 3.0 or cost_drop_ok or latency_drop_ok:
        return "shadow_deploy" if status == "shadow_recommended" else "promote"
    if aes_delta >= 1.0:
        return "make_conditional"
    return "hold"


def to_variant_score(metrics: VariantMetrics, aes: float | None, decision: str = "") -> VariantScore:
    return VariantScore(
        name=metrics.name,
        status=metrics.status,
        status_reason=metrics.status_reason,
        aes=round_score(aes),
        task_success=round_score(metrics.category_scores.task_success) if aes is not None else None,
        rag_quality=round_score(metrics.category_scores.rag_quality) if aes is not None else None,
        orchestration=round_score(metrics.category_scores.orchestration_effectiveness) if aes is not None else None,
        learning=round_score(metrics.category_scores.continual_learning_value) if aes is not None else None,
        efficiency=round_score(metrics.category_scores.efficiency) if aes is not None else None,
        safety=round_score(metrics.category_scores.safety_groundedness) if aes is not None else None,
        maintainability=round_score(metrics.category_scores.maintainability) if aes is not None else None,
        p95_latency_ms=round_score(metrics.p95_latency_ms),
        cost_per_task=round_score(metrics.cost_per_task, 4),
        decision=decision,
    )


def build_dashboard_summary(
    *,
    run_id: str,
    timestamp: str,
    status: str,
    winner_key: str,
    production_key: str,
    score_map: dict[str, float | None],
    metric_map: dict[str, VariantMetrics],
    swarm_advantage_score: float | None,
    complexity_tax: ComplexityTaxBreakdown,
    recommendation: str,
    report_path: str,
    top_actions: list[str],
) -> DashboardSummary:
    production = metric_map[production_key]
    winner = metric_map[winner_key]
    production_score = score_map.get(production_key)
    winning_score = score_map.get(winner_key)
    delta = None
    if production_score is not None and winning_score is not None:
        delta = round_score(winning_score - production_score)

    return DashboardSummary(
        run_id=run_id,
        timestamp=timestamp,
        status=status,
        winner=winner.name,
        production_score=round_score(production_score),
        winning_score=round_score(winning_score),
        score_delta=delta,
        swarm_advantage_score=round_score(swarm_advantage_score),
        complexity_tax=complexity_tax.level,
        hallucination_rate=winner.safety.hallucination_rate,
        critical_failures=winner.safety.critical_hallucinations,
        p95_latency_ms=winner.p95_latency_ms,
        cost_per_task=winner.cost_per_task,
        recommendation=recommendation,
        report_path=report_path,
        dashboard_badge=dashboard_badge_from_status(status),
        top_actions=top_actions,
    )


def build_ablation_row(
    component: str,
    with_score: float | None,
    without_score: float | None,
    status: str = "ok",
    reason: str | None = None,
) -> ComponentAblationResult:
    delta = None
    if with_score is not None and without_score is not None:
        delta = round_score(with_score - without_score)
    return ComponentAblationResult(
        component=component,
        with_score=round_score(with_score),
        without_score=round_score(without_score),
        delta=delta,
        recommendation=ablation_recommendation(delta),
        status=status,
        reason=reason,
    )


def aggregate_safety(metrics: Iterable[VariantMetrics]) -> SafetyMetrics:
    items = [m.safety for m in metrics]
    if not items:
        return SafetyMetrics()
    return SafetyMetrics(
        hallucination_rate=max(item.hallucination_rate for item in items),
        critical_hallucinations=max(item.critical_hallucinations for item in items),
        fabricated_tool_call_claims=max(item.fabricated_tool_call_claims for item in items),
        tool_call_validity=min(item.tool_call_validity for item in items),
        citation_validity=min(item.citation_validity for item in items),
    )

