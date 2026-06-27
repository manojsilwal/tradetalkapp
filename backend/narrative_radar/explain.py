"""
Deterministic explanation generator for the Narrative Rotation Radar (Plan §15).

Produces the top positive / negative drivers and a compliance-safe one-line summary
for a scored theme. No fabricated numbers: drivers are derived only from present
feature values and computed scores. Language uses research framing — never
buy/sell/hold.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import lifecycle as lc
from . import themes as nr_themes
from .scoring import DEFERRED_FAMILIES

DISCLAIMER = (
    "This feature identifies observable market, filing, fund-flow, media, and "
    "institutional-positioning signals. It does not infer intent, coordination, or "
    "manipulation by any institution. Scores are probabilistic research indicators and "
    "may be incomplete or delayed depending on source availability. This is not investment advice."
)

_FAMILY_HUMAN = {
    "institutional_conviction": "institutional 13F footprint",
    "retail_saturation": "retail/media saturation",
    "narrative": "media narrative",
    "narrative_reality_alignment": "fundamentals reality check",
    "productization": "ETF productization",
    "macro_tailwind": "macro regime fit",
}


def _fmt_pct(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    sign = "+" if v > 0 else ""
    return f"{sign}{round(float(v), 1)}%"


def _positive_drivers(feat: Dict[str, Any], scores: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if (feat.get("rs_momentum") or 0) > 0:
        out.append("Relative strength vs SPY is improving (positive RS momentum).")
    if (feat.get("rs_ratio") or 0) > 1.0:
        out.append("Theme basket is outperforming SPY (relative strength above 1.0).")
    p50 = feat.get("pct_above_50dma")
    if p50 is not None and p50 >= 60:
        out.append(f"{round(p50)}% of theme members are above their 50-day average.")
    bp = feat.get("breadth_positive_pct")
    if bp is not None and bp >= 60:
        out.append(f"Broad participation: {round(bp)}% of members have positive 3-month returns.")
    r3 = _fmt_pct(feat.get("median_ret_3m_pct"))
    if r3 and (feat.get("median_ret_3m_pct") or 0) > 0:
        out.append(f"Median member 3-month return is {r3}.")
    return out or ["No strong constructive market signals yet."]


def _negative_drivers(feat: Dict[str, Any], scores: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    spread = feat.get("cap_vs_equal_spread_pct")
    if spread is not None and spread > 2.0:
        out.append(
            f"Narrowing leadership: cap-weighted basket is outpacing the equal-weighted basket "
            f"by {round(spread, 1)}% (a late-cycle breadth red flag)."
        )
    if (feat.get("rs_momentum") or 0) < 0:
        out.append("Relative strength momentum has turned negative.")
    p200 = feat.get("pct_above_200dma")
    if p200 is not None and p200 < 40:
        out.append(f"Only {round(p200)}% of members are above their 200-day average.")
    if (scores.get("theme_exit_risk_score") or 0) >= 70:
        out.append("Exit-risk score is elevated.")
    return out or ["No major risk signals detected in market data."]


def build_explanation(
    theme_id: str,
    feat: Dict[str, Any],
    scored: Dict[str, Any],
    phase: str,
) -> Dict[str, Any]:
    scores = scored.get("scores") or {}
    label = nr_themes.theme_label(theme_id)
    rec = lc.recommendation_label(phase)
    phase_human = lc.phase_label(phase)

    unavailable_human = [
        _FAMILY_HUMAN.get(f, f) for f in (scored.get("unavailable_families") or [])
        if f in DEFERRED_FAMILIES
    ]

    summary = (
        f"{label} is classified as {phase_human} (confidence: {scored.get('confidence_level')}). "
        f"Market confirmation {scores.get('market_confirmation_score')}, "
        f"breadth quality {scores.get('breadth_quality_score')}, "
        f"exit risk {scores.get('theme_exit_risk_score')}."
    )

    return {
        "theme_id": theme_id,
        "theme_label": label,
        "phase": phase,
        "phase_label": phase_human,
        "recommendation_label": rec,
        "summary": summary,
        "top_positive_drivers": _positive_drivers(feat, scores),
        "top_negative_drivers": _negative_drivers(feat, scores),
        "pending_signal_families": unavailable_human,
        "disclaimer": DISCLAIMER,
    }
