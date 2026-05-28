from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .schemas import CategoryScore, TaskResultRow

UBDS_WEIGHTS = {
    "task_success_completion": 0.25,
    "efficiency_flow_friction": 0.15,
    "error_rate_recovery": 0.15,
    "navigation_information_architecture": 0.15,
    "visual_design_consistency": 0.10,
    "accessibility_responsiveness": 0.10,
    "user_satisfaction_trust": 0.10,
}


@dataclass
class AccessibilityInput:
    critical_issues: int = 0
    moderate_issues: int = 0
    contrast_pass_rate: float = 1.0
    mobile_layout_pass_rate: float = 1.0
    keyboard_navigation_success: float = 1.0


@dataclass
class VisualDesignInput:
    design_system_compliance_rate: float = 0.9
    contrast_pass_rate: float = 0.95
    empty_state_coverage: float = 0.85
    loading_state_coverage: float = 0.85
    error_state_coverage: float = 0.85


@dataclass
class SatisfactionInput:
    sus_score: float = 75.0
    csat_score: float = 0.85
    seq_score: float = 6.0
    trust_rating: float = 0.82
    nps: float = 25.0


def clamp_0_100(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


def round_score(v: float | None, nd: int = 2) -> float | None:
    if v is None:
        return None
    return round(float(v), nd)


def grade_from_score(score: float) -> str:
    s = float(score)
    if s >= 95:
        return "A+"
    if s >= 90:
        return "A"
    if s >= 85:
        return "B+"
    if s >= 80:
        return "B"
    if s >= 70:
        return "C"
    if s >= 60:
        return "D"
    return "F"


def score_from_completion_rate(rate: float) -> float:
    pct = rate * 100.0
    if pct >= 95:
        return 100.0
    if pct >= 90:
        return 90.0
    if pct >= 80:
        return 80.0
    if pct >= 70:
        return 65.0
    if pct >= 60:
        return 50.0
    return 30.0


def score_task_success(tasks: Iterable[TaskResultRow]) -> CategoryScore:
    rows = list(tasks)
    if not rows:
        return CategoryScore(score=0.0, weight=UBDS_WEIGHTS["task_success_completion"])
    completed = sum(1 for t in rows if t.completed)
    critical = [t for t in rows if t.critical]
    crit_done = sum(1 for t in critical if t.completed) if critical else completed
    abandon = 1.0 - (completed / len(rows))
    tcr = completed / len(rows)
    csr = crit_done / len(critical) if critical else tcr
    abandon_avoid = clamp_0_100((1.0 - abandon) * 100.0)
    score = clamp_0_100(
        score_from_completion_rate(tcr) * 0.50
        + score_from_completion_rate(csr) * 0.30
        + abandon_avoid * 0.20
    )
    return CategoryScore(
        score=round_score(score) or 0.0,
        weight=UBDS_WEIGHTS["task_success_completion"],
        metrics={
            "task_completion_rate": round(tcr, 4),
            "critical_task_success_rate": round(csr, 4),
            "task_abandonment_rate": round(abandon, 4),
        },
    )


def score_efficiency(tasks: Iterable[TaskResultRow], *, benchmark_median_ms: float = 45000.0) -> CategoryScore:
    rows = [t for t in tasks if t.completed]
    if not rows:
        return CategoryScore(score=50.0, weight=UBDS_WEIGHTS["efficiency_flow_friction"])
    times = sorted(t.time_on_task_ms for t in rows)
    median = times[len(times) // 2]
    p95 = times[int(len(times) * 0.95) - 1] if len(times) > 1 else times[-1]
    ratio = median / benchmark_median_ms if benchmark_median_ms > 0 else 1.0
    if ratio <= 0.8:
        time_score = 100.0
    elif ratio <= 1.0:
        time_score = 85.0
    elif ratio <= 1.2:
        time_score = 70.0
    elif ratio <= 1.4:
        time_score = 55.0
    else:
        time_score = 35.0
    avg_steps = sum(t.steps for t in rows) / len(rows)
    step_eff = clamp_0_100(100.0 - max(0.0, (avg_steps - 5.0) * 8.0))
    backtrack_rate = sum(t.backtrack_count for t in rows) / max(1, len(rows))
    backtrack_score = clamp_0_100(100.0 - backtrack_rate * 25.0)
    score = clamp_0_100(time_score * 0.40 + step_eff * 0.25 + 85.0 * 0.20 + backtrack_score * 0.15)
    return CategoryScore(
        score=round_score(score) or 0.0,
        weight=UBDS_WEIGHTS["efficiency_flow_friction"],
        metrics={
            "median_time_on_task_ms": median,
            "p95_time_on_task_ms": p95,
            "average_steps_to_complete": round(avg_steps, 2),
            "backtrack_rate": round(backtrack_rate, 4),
        },
    )


def score_errors(tasks: Iterable[TaskResultRow]) -> CategoryScore:
    rows = list(tasks)
    if not rows:
        return CategoryScore(score=50.0, weight=UBDS_WEIGHTS["error_rate_recovery"])
    errors = sum(t.error_count for t in rows)
    attempts = len(rows)
    error_rate = errors / max(1, attempts)
    error_free = sum(1 for t in rows if t.completed and t.error_count == 0) / max(1, attempts)
    recovery = sum(1 for t in rows if t.error_count > 0 and t.completed) / max(1, sum(1 for t in rows if t.error_count > 0) or 1)
    if error_rate <= 0.02:
        err_score = 100.0
    elif error_rate <= 0.05:
        err_score = 90.0
    elif error_rate <= 0.10:
        err_score = 75.0
    elif error_rate <= 0.20:
        err_score = 55.0
    else:
        err_score = 30.0
    score = clamp_0_100(
        error_free * 100.0 * 0.35 + err_score * 0.20 + recovery * 100.0 * 0.25 + 80.0 * 0.20
    )
    return CategoryScore(
        score=round_score(score) or 0.0,
        weight=UBDS_WEIGHTS["error_rate_recovery"],
        metrics={
            "error_rate": round(error_rate, 4),
            "recovery_success_rate": round(recovery, 4),
            "error_free_completion_rate": round(error_free, 4),
        },
    )


def score_navigation(tasks: Iterable[TaskResultRow]) -> CategoryScore:
    nav_tasks = [t for t in tasks if t.task_id.startswith("nav_")]
    if not nav_tasks:
        nav_tasks = list(tasks)
    success = sum(1 for t in nav_tasks if t.completed) / max(1, len(nav_tasks))
    first_click_proxy = success
    if success >= 0.90:
        nav_score = 100.0
    elif success >= 0.80:
        nav_score = 85.0
    elif success >= 0.70:
        nav_score = 70.0
    elif success >= 0.60:
        nav_score = 55.0
    else:
        nav_score = 35.0
    score = clamp_0_100(nav_score)
    return CategoryScore(
        score=round_score(score) or 0.0,
        weight=UBDS_WEIGHTS["navigation_information_architecture"],
        metrics={
            "navigation_success_rate": round(success, 4),
            "first_click_success_rate": round(first_click_proxy, 4),
        },
    )


def score_visual(v: VisualDesignInput) -> CategoryScore:
    base = v.design_system_compliance_rate * 100.0
    states = (v.empty_state_coverage + v.loading_state_coverage + v.error_state_coverage) / 3.0 * 100.0
    contrast = v.contrast_pass_rate * 100.0
    score = clamp_0_100(base * 0.55 + states * 0.25 + contrast * 0.20)
    return CategoryScore(
        score=round_score(score) or 0.0,
        weight=UBDS_WEIGHTS["visual_design_consistency"],
        metrics={
            "design_system_compliance_rate": v.design_system_compliance_rate,
            "contrast_pass_rate": v.contrast_pass_rate,
            "empty_state_coverage": v.empty_state_coverage,
            "loading_state_coverage": v.loading_state_coverage,
            "error_state_coverage": v.error_state_coverage,
        },
    )


def score_accessibility(a: AccessibilityInput) -> CategoryScore:
    if a.critical_issues > 0:
        base = 35.0
    elif a.moderate_issues > 6:
        base = 65.0
    elif a.moderate_issues > 0:
        base = 85.0
    else:
        base = 95.0
    mobile = a.mobile_layout_pass_rate * 100.0
    kb = a.keyboard_navigation_success * 100.0
    contrast = a.contrast_pass_rate * 100.0
    score = clamp_0_100(base * 0.50 + mobile * 0.20 + kb * 0.15 + contrast * 0.15)
    return CategoryScore(
        score=round_score(score) or 0.0,
        weight=UBDS_WEIGHTS["accessibility_responsiveness"],
        metrics={
            "critical_accessibility_issues": a.critical_issues,
            "moderate_accessibility_issues": a.moderate_issues,
            "mobile_layout_pass_rate": a.mobile_layout_pass_rate,
            "contrast_pass_rate": a.contrast_pass_rate,
        },
    )


def score_satisfaction(s: SatisfactionInput, tasks: Iterable[TaskResultRow]) -> CategoryScore:
    seq_vals = [t.seq_score for t in tasks if t.seq_score is not None]
    seq_avg = sum(seq_vals) / len(seq_vals) if seq_vals else s.seq_score
    seq_norm = clamp_0_100((seq_avg / 7.0) * 100.0)
    sus_norm = clamp_0_100(s.sus_score)
    csat_norm = clamp_0_100(s.csat_score * 100.0)
    trust_norm = clamp_0_100(s.trust_rating * 100.0)
    nps_norm = clamp_0_100(50.0 + s.nps)
    score = clamp_0_100(
        sus_norm * 0.30 + csat_norm * 0.25 + seq_norm * 0.20 + trust_norm * 0.15 + nps_norm * 0.10
    )
    return CategoryScore(
        score=round_score(score) or 0.0,
        weight=UBDS_WEIGHTS["user_satisfaction_trust"],
        metrics={
            "sus_score": s.sus_score,
            "csat_score": s.csat_score,
            "seq_score": round(seq_avg, 2),
            "trust_rating": s.trust_rating,
            "nps": s.nps,
        },
    )


def calculate_ubds(
    categories: dict[str, CategoryScore],
) -> float:
    total = 0.0
    for key, cat in categories.items():
        w = UBDS_WEIGHTS.get(key, 0.0)
        total += float(cat.score) * w
    return clamp_0_100(total)


def release_gate(
    overall: float,
    categories: dict[str, CategoryScore],
    *,
    critical_issues: int,
    critical_abandon_rate: float,
) -> str:
    tsc = categories.get("task_success_completion", CategoryScore()).score
    acc = categories.get("accessibility_responsiveness", CategoryScore()).score
    err = categories.get("error_rate_recovery", CategoryScore()).score
    if (
        overall < 75
        or tsc < 60
        or critical_issues > 0
        or critical_abandon_rate > 0.15
    ):
        return "fail"
    if overall < 80 or tsc < 85 or acc < 80 or err < 75:
        return "hold"
    return "pass"
