"""
NR-9 — alert generation (Plan §14).

Pure ``generate_alerts(rows)`` turns scored theme rows into alerts using the plan's
threshold rules. Every rule is None-safe (a rule is skipped when its inputs are not
available) so alerts only fire on real signals. Alerts carry a score snapshot for
explainability and feed the alert center + notifications.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Alert types (Plan §6.8).
EMERGING_THEME = "EMERGING_THEME"
ACCUMULATION_SIGNAL = "ACCUMULATION_SIGNAL"
ACCELERATION_SIGNAL = "ACCELERATION_SIGNAL"
SATURATION_WARNING = "SATURATION_WARNING"
DISTRIBUTION_WARNING = "DISTRIBUTION_WARNING"
EXIT_ALERT = "EXIT_ALERT"
STEALTH_ACCUMULATION = "STEALTH_ACCUMULATION"
DISTRIBUTION_INTO_HYPE = "DISTRIBUTION_INTO_HYPE"

_SEVERITY = {
    EMERGING_THEME: "info",
    ACCUMULATION_SIGNAL: "info",
    ACCELERATION_SIGNAL: "info",
    SATURATION_WARNING: "medium",
    DISTRIBUTION_WARNING: "high",
    EXIT_ALERT: "high",
    STEALTH_ACCUMULATION: "info",
    DISTRIBUTION_INTO_HYPE: "high",
}


def _ge(v: Optional[float], thr: float) -> bool:
    return v is not None and v >= thr


def _le(v: Optional[float], thr: float) -> bool:
    return v is not None and v <= thr


def _alerts_for_row(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    s = row.get("scores") or {}
    conf = row.get("confidence_score") or 0
    formation = s.get("theme_formation_score")
    accumulation = s.get("theme_accumulation_score")
    acceleration = s.get("theme_acceleration_score")
    distribution = s.get("theme_distribution_risk_score")
    exit_risk = s.get("theme_exit_risk_score")
    institutional = s.get("institutional_conviction_score")
    breadth = s.get("breadth_quality_score")
    market = s.get("market_confirmation_score")
    retail = s.get("retail_saturation_score")
    divergence = s.get("smart_money_divergence_score")
    retail_dir = s.get("retail_narrative_direction_score")

    out: List[Dict[str, Any]] = []

    def add(alert_type: str, title: str, explanation: str) -> None:
        out.append({
            "alert_type": alert_type,
            "severity": _SEVERITY.get(alert_type, "info"),
            "theme_id": row.get("theme_id"),
            "theme_label": row.get("theme_label"),
            "group": row.get("group"),
            "title": title,
            "explanation": explanation,
            "score_snapshot": s,
        })

    label = row.get("theme_label") or row.get("theme_id")

    if _ge(formation, 65) and _le(retail if retail is not None else 0, 50) and _ge(conf, 45):
        add(EMERGING_THEME, f"Emerging theme: {label}",
            "Formation score is high while retail saturation is still low — an early-watchlist candidate.")
    if (
        _ge(accumulation, 65)
        and _ge(institutional, 60)
        and _ge(breadth, 55)
        and _ge(conf, 55)
        and (divergence is None or _ge(divergence, 65))
    ):
        add(ACCUMULATION_SIGNAL, f"Accumulation signal: {label}",
            "Institutional conviction and breadth suggest accumulation; smart-money divergence supports the read.")
    if _ge(acceleration, 70) and _ge(market, 65) and _ge(breadth, 60):
        add(ACCELERATION_SIGNAL, f"Acceleration confirmed: {label}",
            "Market confirmation and breadth are strong — the trend is confirmed.")
    if _ge(retail, 75):
        add(SATURATION_WARNING, f"Saturation warning: {label}",
            "Retail/media saturation is very high — reward/risk is deteriorating.")
    if (
        _ge(distribution, 70)
        and _ge(retail if retail is not None else 65, 65)
        and (divergence is None or _le(divergence, 35))
    ):
        add(DISTRIBUTION_WARNING, f"Distribution risk: {label}",
            "Narrowing leadership and high saturation point to late-cycle distribution risk.")
    if _ge(exit_risk, 75):
        add(EXIT_ALERT, f"Exit / rotation-away risk: {label}",
            "Relative strength and breadth are deteriorating — avoid chasing.")

    if _ge(divergence, 75) and _le(retail_dir if retail_dir is not None else 50, 45):
        add(
            STEALTH_ACCUMULATION,
            f"Stealth accumulation: {label}",
            "Weeks-fresh smart-money accumulation is elevated while retail narrative "
            "leans bearish or quiet — potential quiet accumulation before headlines.",
        )
    if _le(divergence, 25) and _ge(retail_dir if retail_dir is not None else 55, 60) and _ge(retail if retail is not None else 0, 60):
        add(
            DISTRIBUTION_INTO_HYPE,
            f"Distribution into hype: {label}",
            "Smart-money proxies are weakening while retail narrative is euphoric — "
            "late-cycle distribution risk into media hype.",
        )

    return out


def generate_alerts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generate all alerts across scored theme rows (Plan §14)."""
    alerts: List[Dict[str, Any]] = []
    for row in rows:
        alerts.extend(_alerts_for_row(row))
    # high severity first
    order = {"high": 0, "medium": 1, "info": 2}
    alerts.sort(key=lambda a: order.get(a.get("severity"), 3))
    return alerts
