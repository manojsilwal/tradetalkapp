"""Transparent rule-based composite score (the floor any model must beat).

Produces 0-100 scores per signal group plus a weighted composite, from a
cross-section of feature rows (percentile-ranked within the cross-section). This
is intentionally simple and explainable; if a trained model cannot beat it under
purged CV + backtest, the model is not trusted (docs Rule 07).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from . import SIGNAL_GROUPS

# Composite weights (sum to 1.0).
COMPOSITE_WEIGHTS = {
    "momentum": 0.16,
    "quality": 0.17,
    "valuation": 0.13,
    "capital_flow": 0.11,
    "filing_intelligence": 0.10,
    "sentiment": 0.07,
    "risk": 0.09,
    "timeseries": 0.09,
    "options_flow": 0.08,
}

# Long-horizon (investment-surface) weight profile. For a 1-5 year horizon,
# short-term price behaviour (momentum + sentiment) has weak predictive power, so
# it is intentionally capped at a small total weight; valuation and durable
# business quality dominate. This profile re-weights the SAME transparency group
# scores (no model retrain) and is applied only on the investment surface so the
# existing quarterly surfaces are unchanged. Sums to 1.0 over SIGNAL_GROUPS.
#   valuation 0.28  -> business value vs price (margin of safety)
#   quality   0.24  -> ROIC / margins / growth / leverage (moat + durability)
#   risk      0.10  -> balance-sheet / drawdown resilience
#   capital_flow 0.10 -> institutional accumulation (long-horizon smart money)
#   filing_intelligence 0.10 -> filing-risk / going-concern language
#   timeseries 0.05 -> forward forecast context
#   momentum  0.05  -> pricing context only (NOT a trading signal)
#   sentiment 0.05  -> short-term sentiment context only
LONG_HORIZON_COMPOSITE_WEIGHTS = {
    "valuation": 0.28,
    "quality": 0.24,
    "risk": 0.10,
    "capital_flow": 0.10,
    "filing_intelligence": 0.10,
    "timeseries": 0.05,
    "momentum": 0.04,
    "sentiment": 0.04,
    "options_flow": 0.05,
}


def composite_from_group_scores(
    group_scores: "Dict[str, float]",
    weights: "Optional[Dict[str, float]]" = None,
) -> "Optional[float]":
    """Recombine existing 0-100 group scores with a weight profile.

    Pure and side-effect free: it does NOT touch the trained model — it only
    re-weights the already-computed transparency group scores. Returns ``None``
    when no group score is available (so callers surface ``insufficient_data``
    rather than a fabricated number).
    """
    w = weights or COMPOSITE_WEIGHTS
    num = wsum = 0.0
    for g in SIGNAL_GROUPS:
        s = group_scores.get(g)
        if s is None:
            continue
        gw = float(w.get(g, 0.0))
        num += gw * float(s)
        wsum += gw
    if wsum <= 0:
        return None
    return round(num / wsum, 2)

# Each group is a list of (feature, weight, invert). ``invert`` means lower raw
# value is better (e.g. volatility, debt) so we use 1 - percentile.
_GROUP_DEFS = {
    "momentum": [
        ("return_3m", 0.30, False),
        ("return_6m", 0.25, False),
        ("return_12m", 0.20, False),
        ("relative_strength_3m", 0.15, False),
        ("price_vs_200dma", 0.10, False),
    ],
    "quality": [
        ("roic", 0.25, False),
        ("operating_margin", 0.20, False),
        ("fcf_margin", 0.20, False),
        ("revenue_growth_yoy", 0.15, False),
        ("net_margin", 0.10, False),
        ("debt_to_equity", 0.10, True),
    ],
    "valuation": [
        ("fcf_yield", 0.35, False),
        ("pe_ratio", 0.30, True),
        ("ev_ebitda", 0.20, True),
        ("valuation_percentile_5y", 0.15, True),
    ],
    "capital_flow": [
        ("capital_flow_score", 0.6, False),
        ("institutional_accumulation_score", 0.4, False),
    ],
    "filing_intelligence": [
        ("new_product_expansion_score", 0.30, False),
        ("management_tone_score", 0.25, False),
        ("filing_risk_score", 0.25, True),
        ("demand_visibility_score", 0.20, False),
    ],
    "sentiment": [
        ("sentiment_score", 1.0, False),
    ],
    "timeseries": [
        # TimesFM forward view: higher expected return is better; a wider
        # (more uncertain) forecast band is worse.
        ("tsfm_expected_return", 0.7, False),
        ("tsfm_band_width", 0.3, True),
    ],
    "risk": [
        ("volatility_3m", 0.40, True),
        # max_drawdown_6m is negative; a value closer to 0 (shallower) is safer,
        # so higher raw value is better -> do NOT invert.
        ("max_drawdown_6m", 0.30, False),
        ("customer_concentration_score", 0.30, True),
    ],
    "options_flow": [
        ("options_net_premium_bias_num", 0.30, False),
        ("put_call_volume_ratio", 0.25, True),
        ("put_call_oi_ratio", 0.20, True),
        ("unusual_activity_score", 0.15, False),
        ("iv_skew", 0.10, True),
    ],
}


def _percentile_matrix(rows: List[Dict[str, Optional[float]]], feature: str) -> np.ndarray:
    """Cross-sectional percentile (0-1) of ``feature`` across rows; NaN where missing.

    ``max_drawdown_6m`` is negative (closer to 0 is better); percentile handles it
    naturally because we percentile the raw value then optionally invert.
    """
    vals = np.array([
        (r.get(feature) if r.get(feature) is not None else np.nan) for r in rows
    ], dtype=float)
    out = np.full(vals.shape, np.nan)
    mask = ~np.isnan(vals)
    if mask.sum() == 0:
        return out
    valid = vals[mask]
    # percentile rank of each valid value within the valid population
    order = valid.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, valid.size + 1)
    pct = ranks / valid.size
    out[mask] = pct
    return out


def score_cross_section(rows: List[Dict[str, Optional[float]]]) -> List[Dict[str, float]]:
    """Return per-row dict of {group: 0-100, ..., 'composite_score': 0-100}."""
    n = len(rows)
    if n == 0:
        return []

    # Precompute percentile vectors per feature used by any group.
    feats = {f for defs in _GROUP_DEFS.values() for (f, _, _) in defs}
    pct = {f: _percentile_matrix(rows, f) for f in feats}

    results: List[Dict[str, float]] = [dict() for _ in range(n)]
    for group, defs in _GROUP_DEFS.items():
        for i in range(n):
            num = 0.0
            wsum = 0.0
            for feature, w, invert in defs:
                v = pct[feature][i]
                if np.isnan(v):
                    continue
                score = (1.0 - v) if invert else v
                num += w * score
                wsum += w
            group_score = (num / wsum) if wsum > 0 else 0.5  # neutral if no data
            results[i][group] = round(100.0 * group_score, 2)

    for i in range(n):
        composite = sum(COMPOSITE_WEIGHTS[g] * results[i].get(g, 50.0) for g in SIGNAL_GROUPS)
        results[i]["composite_score"] = round(composite, 2)
    return results
