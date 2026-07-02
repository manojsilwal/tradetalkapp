"""Post-inference overlay: apply filing intelligence to brain group scores."""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import SIGNAL_GROUPS, rule_baseline
from .filing_intelligence_scorer import (
    apply_concentration_risk_modifier,
    apply_revenue_quality_modifier,
    brain_features_from_record,
    compute_filing_group_score_0_100,
)


def _recompute_composite(signal_scores: Dict[str, float]) -> float:
    return round(
        sum(
            rule_baseline.COMPOSITE_WEIGHTS.get(g, 0.0) * signal_scores.get(g, 50.0)
            for g in SIGNAL_GROUPS
        ),
        2,
    )


def apply_filing_overlay(
    brain_result: Dict[str, Any],
    record: Optional[Dict[str, Any]],
    *,
    fundamentals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge filing record into live signal_scores without retraining the ML model."""
    if not record or not brain_result:
        return brain_result

    out = dict(brain_result)
    live = dict(out.get("live") or out.get("base") or {})
    signal_scores = dict(live.get("signal_scores") or {})

    features = brain_features_from_record(record)
    if features:
        filing_group = compute_filing_group_score_0_100(features)
        signal_scores["filing_intelligence"] = filing_group

        conc = features.get("customer_concentration_score")
        if conc is not None:
            signal_scores["risk"] = apply_concentration_risk_modifier(
                signal_scores.get("risk"), conc
            )

    fund = fundamentals or {}
    rev_g = fund.get("revenue_growth_yoy") or fund.get("revenueGrowth")
    if rev_g is not None and abs(float(rev_g)) > 1.5:
        rev_g = float(rev_g) / 100.0
    gm = fund.get("gross_margin") or fund.get("grossMargins")
    de = fund.get("debt_to_equity") or fund.get("debtToEquity")
    signal_scores["quality"] = apply_revenue_quality_modifier(
        signal_scores.get("quality"),
        revenue_growth_yoy=float(rev_g) if rev_g is not None else None,
        gross_margin=float(gm) if gm is not None else None,
        debt_to_equity=float(de) if de is not None else None,
    )

    signal_scores["composite_score"] = _recompute_composite(signal_scores)
    live["signal_scores"] = signal_scores
    live["composite_score"] = signal_scores["composite_score"]
    if out.get("live"):
        out["live"] = live
    else:
        out["base"] = live
    out["filing_intelligence"] = record
    return out
