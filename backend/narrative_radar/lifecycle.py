"""
Deterministic lifecycle-phase classifier (Plan §4, §8.5).

Maps a theme's scores to one of the eight lifecycle phases (plus a low-confidence
watchlist fallback) and a compliance-safe recommendation label (Plan §11.6 — never
buy/sell/hold). The MVP version is purely deterministic; ML calibration is deferred
until backtests exist (the ledger + outcome_grader provide that for free).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Lifecycle phases.
DISCOVERY_SEEDING = "DISCOVERY_SEEDING"
EARLY_ACCUMULATION = "EARLY_ACCUMULATION"
ACCELERATION = "ACCELERATION"
MAINSTREAM_MOMENTUM = "MAINSTREAM_MOMENTUM"
SATURATION_CROWDING = "SATURATION_CROWDING"
DISTRIBUTION_RISK = "DISTRIBUTION_RISK"
EXIT_ROTATION_AWAY = "EXIT_ROTATION_AWAY"
DORMANT_REBASE = "DORMANT_REBASE"
LOW_CONFIDENCE_WATCHLIST = "LOW_CONFIDENCE_WATCHLIST"

# Compliance-safe label per phase (Plan §11.6).
RECOMMENDATION_LABELS: Dict[str, str] = {
    DISCOVERY_SEEDING: "Early Watchlist",
    EARLY_ACCUMULATION: "Accumulation Candidate",
    ACCELERATION: "Confirmed Momentum",
    MAINSTREAM_MOMENTUM: "Crowded Momentum",
    SATURATION_CROWDING: "Crowded Momentum",
    DISTRIBUTION_RISK: "Distribution Risk",
    EXIT_ROTATION_AWAY: "Exit / Avoid Chase",
    DORMANT_REBASE: "Dormant / Rebase",
    LOW_CONFIDENCE_WATCHLIST: "Low Confidence",
}

PHASE_LABELS: Dict[str, str] = {
    DISCOVERY_SEEDING: "Discovery / Seeding",
    EARLY_ACCUMULATION: "Early Accumulation",
    ACCELERATION: "Acceleration",
    MAINSTREAM_MOMENTUM: "Mainstream Momentum",
    SATURATION_CROWDING: "Saturation / Crowding",
    DISTRIBUTION_RISK: "Distribution Risk",
    EXIT_ROTATION_AWAY: "Exit / Rotation Away",
    DORMANT_REBASE: "Dormant / Rebase",
    LOW_CONFIDENCE_WATCHLIST: "Low Confidence Watchlist",
}

# Confidence floor below which we will not assert a phase.
_MIN_CONFIDENCE = 35.0


def _g(scores: Dict[str, Any], key: str, default: float = 50.0) -> float:
    v = scores.get(key)
    return float(v) if v is not None else default


def classify_theme_phase(scores: Dict[str, Any], confidence_score: float) -> str:
    """
    Deterministic phase classification (Plan §8.5). Order matters: risk states are
    evaluated before constructive states so a weakening leader is flagged early.

    ``scores`` keys: theme_formation_score, theme_accumulation_score,
    theme_acceleration_score, theme_distribution_risk_score, theme_exit_risk_score,
    retail_saturation_score (may be None in MVP → treated neutral).
    """
    if confidence_score < _MIN_CONFIDENCE:
        return LOW_CONFIDENCE_WATCHLIST

    formation = _g(scores, "theme_formation_score")
    accumulation = _g(scores, "theme_accumulation_score")
    acceleration = _g(scores, "theme_acceleration_score")
    distribution = _g(scores, "theme_distribution_risk_score")
    exit_risk = _g(scores, "theme_exit_risk_score")
    retail = _g(scores, "retail_saturation_score")  # neutral 50 until NR-7

    if exit_risk >= 75:
        return EXIT_ROTATION_AWAY
    if distribution >= 70:
        return DISTRIBUTION_RISK
    if retail >= 75 and acceleration >= 65:
        return MAINSTREAM_MOMENTUM
    if acceleration >= 70:
        return ACCELERATION
    if accumulation >= 65:
        return EARLY_ACCUMULATION
    if formation >= 60:
        return DISCOVERY_SEEDING
    return DORMANT_REBASE


def recommendation_label(phase: str) -> str:
    return RECOMMENDATION_LABELS.get(phase, "Low Confidence")


def phase_label(phase: str) -> str:
    return PHASE_LABELS.get(phase, phase)
