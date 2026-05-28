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


def pinball_loss(realized: float, quantile: float, tau: float) -> float:
    """Pinball / quantile loss for a single observation."""
    err = float(realized) - float(quantile)
    t = float(tau)
    return err * t if err >= 0 else err * (t - 1.0)


def mean_pinball(
    realized: Iterable[float],
    quantiles: Iterable[float],
    tau: float,
) -> float:
    pairs = list(zip(realized, quantiles, strict=False))
    if not pairs:
        return 0.0
    return sum(pinball_loss(y, q, tau) for y, q in pairs) / float(len(pairs))


def interval_pinball_mean(
    realized: float,
    q10: float,
    q50: float,
    q90: float,
) -> float:
    """Average pinball loss across q10 / q50 / q90 for one row."""
    return (
        pinball_loss(realized, q10, 0.10)
        + pinball_loss(realized, q50, 0.50)
        + pinball_loss(realized, q90, 0.90)
    ) / 3.0
