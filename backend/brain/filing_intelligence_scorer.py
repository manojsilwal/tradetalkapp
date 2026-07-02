"""Rule-based scoring for filing-derived features (no ML retrain required).

Uses fixed bounds (_linscore) instead of z-score sigmoid so single-ticker
serving stays calibrated when filing intelligence is injected at request time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Sub-feature weights inside filing_intelligence group (sum 1.0).
FILING_GROUP_WEIGHTS: List[Tuple[str, float, bool]] = [
    ("new_product_expansion_score", 0.30, False),
    ("management_tone_score", 0.25, False),
    ("filing_risk_score", 0.25, True),
    ("demand_visibility_score", 0.20, False),
]

# Post-inference overlay: max delta applied to any single group score.
OVERLAY_MAX_DELTA = 15.0


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _linscore(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    if value is None:
        return None
    if hi <= lo:
        return 0.5
    return _clamp((float(value) - lo) / (hi - lo))


def compute_demand_visibility_score(record: Dict[str, Any]) -> Optional[float]:
    """Composite 0-1 demand visibility from structured KPIs."""
    parts: List[Tuple[float, float]] = []
    btb = record.get("book_to_bill_ratio")
    if btb is not None:
        s = _linscore(btb, 0.8, 1.4)
        if s is not None:
            parts.append((0.35, s))
    bg = record.get("backlog_growth_yoy_pct")
    if bg is not None:
        s = _linscore(bg, -10.0, 40.0)
        if s is not None:
            parts.append((0.30, s))
    rr = record.get("recurring_revenue_pct")
    if rr is not None:
        s = _linscore(rr, 0.0, 60.0)
        if s is not None:
            parts.append((0.20, s))
    backlog = record.get("order_backlog_usd")
    if backlog is not None and float(backlog) > 0:
        parts.append((0.15, 0.7))
    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    return sum(w * s for w, s in parts) / wsum if wsum > 0 else None


def compute_filing_group_score_0_100(features: Dict[str, Optional[float]]) -> float:
    """Return 0-100 filing_intelligence group score from normalized 0-1 features."""
    num = wsum = 0.0
    for feat, w, invert in FILING_GROUP_WEIGHTS:
        raw = features.get(feat)
        if raw is None:
            continue
        v = _clamp(float(raw))
        score = (1.0 - v) if invert else v
        num += w * score
        wsum += w
    if wsum <= 0:
        return 50.0
    return round(100.0 * num / wsum, 2)


def brain_features_from_record(record: Dict[str, Any]) -> Dict[str, float]:
    """Map a FilingIntelligenceRecord dict to brain passthrough keys (0-1)."""
    out: Dict[str, float] = {}
    for key in (
        "filing_risk_score",
        "management_tone_score",
        "new_product_expansion_score",
        "customer_concentration_score",
    ):
        v = record.get(key)
        if v is not None:
            out[key] = _clamp(float(v))
    dvs = record.get("demand_visibility_score")
    if dvs is None:
        dvs = compute_demand_visibility_score(record)
    if dvs is not None:
        out["demand_visibility_score"] = _clamp(float(dvs))
    return out


def apply_revenue_quality_modifier(
    quality_score: Optional[float],
    *,
    revenue_growth_yoy: Optional[float],
    gross_margin: Optional[float],
    debt_to_equity: Optional[float],
    prior_gross_margin: Optional[float] = None,
) -> Optional[float]:
    """Penalize speculative growth; reward margin-expanding organic growth."""
    if quality_score is None:
        return None
    base = float(quality_score)
    if revenue_growth_yoy is None:
        return base
    growth = float(revenue_growth_yoy)
    if growth < 0.15:
        return base
    penalty = 0.0
    if gross_margin is not None and prior_gross_margin is not None:
        if gross_margin < prior_gross_margin - 0.01:
            penalty += 4.0
    elif gross_margin is not None and gross_margin < 0.15 and growth > 0.30:
        penalty += 3.0
    if debt_to_equity is not None and float(debt_to_equity) > 150:
        penalty += 4.0
    boost = 0.0
    if gross_margin is not None and gross_margin >= 0.25 and 0.05 <= growth <= 0.20:
        boost += 3.0
    delta = max(-OVERLAY_MAX_DELTA, min(OVERLAY_MAX_DELTA, boost - penalty))
    return round(base + delta, 2)


def apply_concentration_risk_modifier(
    risk_score: Optional[float],
    concentration: Optional[float],
) -> Optional[float]:
    """Lower risk group score when customer concentration is high (0-1, higher=worse)."""
    if risk_score is None or concentration is None:
        return risk_score
    base = float(risk_score)
    c = _clamp(float(concentration))
    if c < 0.4:
        return base
    penalty = min(OVERLAY_MAX_DELTA, (c - 0.4) * 25.0)
    return round(max(0.0, base - penalty), 2)
