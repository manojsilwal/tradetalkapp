"""Consensus valuation gap, signal, and case assessments for the decision terminal."""
from __future__ import annotations

from typing import Optional, Tuple


def valuation_gap_pct(price: float, fair: float) -> Optional[float]:
    """Percent above (+) or below (−) base fair value: (price − fair) / fair × 100."""
    if fair <= 0:
        return None
    return round((price - fair) / fair * 100.0, 1)


def implied_downside_pct(price: float, fair: float) -> Optional[float]:
    """Return to fair value from current price: (fair − price) / price × 100."""
    if price <= 0:
        return None
    return round((fair - price) / price * 100.0, 1)


def margin_of_safety_pct(price: float, fair: float) -> Optional[float]:
    """Legacy margin-of-safety: (fair − price) / fair × 100 (positive = undervalued)."""
    if fair <= 0:
        return None
    return round((fair - price) / fair * 100.0, 2)


def _spot_vs_level_pct(price: float, level: float) -> Optional[float]:
    if level <= 0:
        return None
    return (price - level) / level * 100.0


def case_assessments(
    price: Optional[float],
    dcf_bear: Optional[float],
    dcf_bull: Optional[float],
) -> Tuple[str, str]:
    """Human-readable bull/bear scenario labels relative to spot."""
    bull_label = ""
    bear_label = ""
    if price and dcf_bull:
        above_bull = _spot_vs_level_pct(price, dcf_bull)
        if above_bull is not None:
            if above_bull <= 10:
                bull_label = "Near fair value"
            elif above_bull <= 25:
                bull_label = "Moderately above bull case"
            else:
                bull_label = "Above bull case"
    if price and dcf_bear:
        above_bear = _spot_vs_level_pct(price, dcf_bear)
        if above_bear is not None:
            if above_bear > 40:
                bear_label = "Significantly overvalued"
            elif above_bear > 15:
                bear_label = "Moderately above bear case"
            else:
                bear_label = "Within bear–base range"
    return bull_label, bear_label


def valuation_signal_label(
    gap_pct: Optional[float],
    price: Optional[float],
    dcf_bull: Optional[float],
) -> str:
    """Graduated signal; softens when spot is near the DCF bull case."""
    if gap_pct is None:
        return "Insufficient data"

    bull_near_fair = False
    if price and dcf_bull and dcf_bull > 0:
        above_bull = _spot_vs_level_pct(price, dcf_bull)
        bull_near_fair = above_bull is not None and above_bull <= 10

    if abs(gap_pct) <= 5:
        return "Near Fair Value"

    if gap_pct > 0:
        if gap_pct > 40 and not bull_near_fair:
            return "Significantly Overvalued"
        if gap_pct > 12 or (gap_pct > 5 and not bull_near_fair):
            return "Moderately Overvalued"
        return "Slightly Overvalued"

    if gap_pct < -40:
        return "Significantly Undervalued"
    if gap_pct < -12:
        return "Moderately Undervalued"
    return "Slightly Undervalued"


def valuation_confidence_label(
    fair_model_count: int,
    dcf_available: bool,
    dcf_bear: Optional[float],
    dcf_bull: Optional[float],
    dcf_base: Optional[float],
    fair_values: list[float],
) -> str:
    """Low / Medium / High based on model coverage and agreement."""
    if fair_model_count == 0:
        return "Low"

    spread_pct: Optional[float] = None
    if len(fair_values) >= 2:
        lo, hi = min(fair_values), max(fair_values)
        mid = (lo + hi) / 2.0
        if mid > 0:
            spread_pct = (hi - lo) / mid * 100.0

    dcf_width_pct: Optional[float] = None
    if dcf_bear and dcf_bull and dcf_base and dcf_base > 0:
        dcf_width_pct = (dcf_bull - dcf_bear) / dcf_base * 100.0

    if fair_model_count >= 2 and spread_pct is not None and spread_pct <= 25:
        if dcf_available and dcf_width_pct is not None and dcf_width_pct <= 60:
            return "High"
        return "Medium"

    if fair_model_count >= 2 or dcf_available:
        return "Medium"
    return "Low"
