from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CategoryScore(BaseModel):
    score: float = 0.0
    weight: float = 0.0
    metrics: dict[str, Any] = Field(default_factory=dict)


class TaskResultRow(BaseModel):
    task_id: str
    task_name: str = ""
    completed: bool = False
    time_on_task_ms: float = 0.0
    error_count: int = 0
    steps: int = 0
    backtrack_count: int = 0
    seq_score: float | None = None
    critical: bool = True


class UbdsRunResult(BaseModel):
    run_id: str
    timestamp: str
    app_name: str = "TradeTalk"
    version: str = "v0.0.0"
    benchmark_type: str = "internal"
    overall_ui_behavior_design_score: float = 0.0
    grade: str = "F"
    status: str = "hold"
    scores: dict[str, CategoryScore] = Field(default_factory=dict)
    task_results: list[TaskResultRow] = Field(default_factory=list)
    top_strengths: list[str] = Field(default_factory=list)
    top_issues: list[str] = Field(default_factory=list)
    recommendation: str = ""
    missing_inputs: list[str] = Field(default_factory=list)
    dashboard_notification: dict[str, Any] = Field(default_factory=dict)
    previous_overall_score: float | None = None
    overall_score_delta: float | None = None
