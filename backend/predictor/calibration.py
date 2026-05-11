"""Calibration helpers — empirical interval coverage vs targets."""

from __future__ import annotations

from typing import Iterable, List, Tuple

from .config_loader import load_yaml_cached


def q10_q90_hit(realized: float, q10: float, q90: float) -> bool:
    lo, hi = (q10, q90) if q10 <= q90 else (q90, q10)
    return lo <= realized <= hi


def empirical_coverage_fraction(hits: Iterable[bool]) -> float:
    h = list(hits)
    if not h:
        return 0.0
    return sum(1 for x in h if x) / float(len(h))


def calibration_band() -> Tuple[float, float]:
    """Return ``(lower, upper)`` acceptable coverage for central 80 % interval."""
    th = load_yaml_cached("predictor_thresholds.yaml")
    lo = float(th.get("calibration_lower") or 0.75)
    hi = float(th.get("calibration_upper") or 0.85)
    return lo, hi


def coverage_in_band(measured: float) -> bool:
    lo, hi = calibration_band()
    return lo <= measured <= hi


def downgrade_confidence_if_miscalibrated(
    measured_coverage: float,
    *,
    base: str = "medium",
) -> str:
    """Map nominal confidence tier when rolling coverage drifts outside band."""
    if coverage_in_band(measured_coverage):
        return base
    return "low"
