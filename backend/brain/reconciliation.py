"""Reconcile business value and market pricing.

Primary output is a quadrant, not a second "final score". This keeps the product
honest: valuation says what the business is worth; pricing says what the market
is doing; the quadrant describes the setup.
"""
from __future__ import annotations

from typing import Dict, Optional


def _score_from_margin(margin: Optional[float]) -> Optional[float]:
    if margin is None:
        return None
    return max(0.0, min(100.0, 50.0 + float(margin) * 100.0))


def _pricing_score(pricing: Optional[Dict]) -> Optional[float]:
    if not pricing:
        return None
    for key in ("momentum_score", "pricing_momentum_score", "relative_strength_score"):
        if pricing.get(key) is not None:
            return float(pricing[key])
    return None


def reconcile_value_price(valuation: Dict, pricing: Optional[Dict] = None,
                          *, risk_score: Optional[float] = None) -> Dict:
    """Return quadrant + plain-English interpretation."""
    margin = valuation.get("margin_of_safety_base")
    valuation_score = valuation.get("valuation_score")
    if valuation_score is None:
        valuation_score = _score_from_margin(margin)
    ps = _pricing_score(pricing)

    undervalued = (margin is not None and margin >= 0.15) or (valuation_score is not None and valuation_score >= 65)
    overvalued = (margin is not None and margin <= -0.15) or (valuation_score is not None and valuation_score <= 35)
    strong_pricing = ps is not None and ps >= 65
    weak_pricing = ps is not None and ps <= 40
    high_risk = risk_score is not None and risk_score >= 0.70

    if high_risk and (valuation_score is None or valuation_score < 75):
        quadrant = "speculative_volatile"
        verdict = "watch"
        reason = "Risk is high enough that valuation attractiveness needs stronger confirmation."
    elif undervalued and strong_pricing:
        quadrant = "compounder_with_momentum"
        verdict = "constructive"
        reason = "Business value appears attractive and market pricing confirms momentum."
    elif undervalued:
        quadrant = "undervalued_opportunity"
        verdict = "constructive"
        reason = "Business value appears above market price, but pricing confirmation is limited."
    elif overvalued and strong_pricing:
        quadrant = "overvalued_hype"
        verdict = "neutral"
        reason = "Market pricing is strong, but the business value gap looks stretched."
    elif overvalued:
        quadrant = "avoid_or_wait"
        verdict = "cautious"
        reason = "Market price appears high relative to estimated business value."
    elif weak_pricing:
        quadrant = "fair_value_weak_timing"
        verdict = "neutral"
        reason = "Valuation is not extreme, while pricing/timing is weak."
    else:
        quadrant = "fair_value"
        verdict = "neutral"
        reason = "Valuation and pricing are not sending a strong asymmetric signal."

    confidence_inputs = [
        1.0 if valuation.get("status") == "ok" else 0.35,
        0.75 if ps is not None else 0.50,
    ]
    if risk_score is not None:
        confidence_inputs.append(max(0.2, 1.0 - 0.5 * risk_score))
    confidence = sum(confidence_inputs) / len(confidence_inputs)
    return {
        "quadrant": quadrant,
        "verdict_bias": verdict,
        "valuation_score": round(valuation_score, 2) if valuation_score is not None else None,
        "pricing_score": round(ps, 2) if ps is not None else None,
        "risk_score": risk_score,
        "confidence": round(confidence, 4),
        "reason": reason,
    }
