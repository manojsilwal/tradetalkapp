from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CategoryScores(BaseModel):
    task_success: float = 0.0
    rag_quality: float = 0.0
    orchestration_effectiveness: float = 0.0
    continual_learning_value: float = 0.0
    efficiency: float = 0.0
    safety_groundedness: float = 0.0
    maintainability: float = 0.0


class SafetyMetrics(BaseModel):
    hallucination_rate: float = 0.0
    critical_hallucinations: int = 0
    fabricated_tool_call_claims: int = 0
    tool_call_validity: float = 1.0
    citation_validity: float = 1.0


class VariantMetrics(BaseModel):
    name: str
    status: str = "ok"
    status_reason: str | None = None
    p95_latency_ms: float = 0.0
    cost_per_task: float = 0.0
    category_scores: CategoryScores = Field(default_factory=CategoryScores)
    safety: SafetyMetrics = Field(default_factory=SafetyMetrics)
    extras: dict[str, Any] = Field(default_factory=dict)


class VariantScore(BaseModel):
    name: str
    status: str
    status_reason: str | None = None
    aes: float | None = None
    task_success: float | None = None
    rag_quality: float | None = None
    orchestration: float | None = None
    learning: float | None = None
    efficiency: float | None = None
    safety: float | None = None
    maintainability: float | None = None
    p95_latency_ms: float | None = None
    cost_per_task: float | None = None
    decision: str = ""


class ComponentAblationResult(BaseModel):
    component: str
    with_score: float | None = None
    without_score: float | None = None
    delta: float | None = None
    recommendation: str = ""
    status: str = "ok"
    reason: str | None = None


class DashboardBadge(BaseModel):
    label: str
    color: str


class DashboardSummary(BaseModel):
    run_id: str
    timestamp: str
    status: str
    winner: str
    production_score: float | None = None
    winning_score: float | None = None
    score_delta: float | None = None
    swarm_advantage_score: float | None = None
    complexity_tax: str = "unknown"
    hallucination_rate: float = 0.0
    critical_failures: int = 0
    p95_latency_ms: float = 0.0
    cost_per_task: float = 0.0
    recommendation: str
    report_path: str
    dashboard_badge: DashboardBadge
    top_actions: list[str] = Field(default_factory=list)


class EvaluationRunResult(BaseModel):
    run_id: str
    timestamp: str
    production_version: str
    benchmark_suite: str
    decision: str
    winner: str
    scores: dict[str, dict[str, Any]]
    swarm_advantage_score: float | None = None
    complexity_tax: str = "unknown"
    ablation_results: list[ComponentAblationResult] = Field(default_factory=list)
    safety: SafetyMetrics = Field(default_factory=SafetyMetrics)
    recommendations: list[str] = Field(default_factory=list)
    dashboard_notification: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    skipped_tests: list[dict[str, str]] = Field(default_factory=list)
    variant_scores: list[VariantScore] = Field(default_factory=list)

