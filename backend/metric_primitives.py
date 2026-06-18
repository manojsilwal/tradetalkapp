"""
Shared metric primitives for the Stock Analysis page.

Pure functions — no I/O. Unit convention policy:
- FCF yield: decimal in API (0.042 = 4.2%); percent for display
- Gross margin: ratio internally (0–1); percent for labels
- ROE / ROIC proxy: percent (18.5 = 18.5%)
- P/E: raw multiple
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GrossMargin:
    ratio: float   # 0.0–1.0 for math
    percent: float  # 0–100 for display


def roic_proxy(roe_pct: float) -> float:
    """ROIC proxy = 0.8 × ROE when ROE > 0."""
    if roe_pct > 0:
        return round(roe_pct * 0.8, 1)
    return 0.0


def fcf_yield_decimal(fcf: Optional[float], market_cap: Optional[float]) -> Optional[float]:
    if fcf is None or market_cap is None or market_cap <= 0:
        return None
    try:
        y = float(fcf) / float(market_cap)
        return round(y, 4)
    except (TypeError, ValueError):
        return None


def fcf_yield_percent(fcf: Optional[float], market_cap: Optional[float]) -> Optional[float]:
    d = fcf_yield_decimal(fcf, market_cap)
    return round(d * 100.0, 2) if d is not None else None


def normalize_gross_margin(raw: Optional[float]) -> Optional[GrossMargin]:
    if raw is None:
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    if x > 1.0:
        return GrossMargin(ratio=x / 100.0, percent=x)
    return GrossMargin(ratio=x, percent=x * 100.0)


def format_usd_compact(n: Optional[float]) -> str:
    if n is None:
        return "N/A"
    x = float(n)
    ax = abs(x)
    if ax >= 1e9:
        return f"${x / 1e9:.2f}B"
    if ax >= 1e6:
        return f"${x / 1e6:.2f}M"
    if ax >= 1e3:
        return f"${x / 1e3:.2f}K"
    return f"${x:.2f}"


def verdict_tone(label: str) -> str:
    """Map any verdict string → strong_positive | positive | neutral | caution | negative."""
    s = (label or "").strip().upper()
    if not s:
        return "neutral"

    strong_positive_terms = (
        "STRONG BUY", "EXCEPTIONAL", "UNDERVALUED", "STRONG_BUY",
    )
    for term in strong_positive_terms:
        if term in s:
            return "strong_positive"

    negative_terms = (
        "SELL", "AVOID", "OVERVALUED", "BEARISH", "REJECTED",
    )
    for term in negative_terms:
        if term in s:
            return "negative"

    caution_terms = ("CAUTION", "STRETCHED", "WEAK", "LIMITED")
    for term in caution_terms:
        if term in s:
            return "caution"

    positive_terms = ("BUY", "FAVORABLE", "BULLISH", "UNDERVALUED")
    for term in positive_terms:
        if term in s:
            return "positive"

    neutral_terms = ("NEUTRAL", "BALANCED", "NEAR FAIR", "HOLD", "MODERATE")
    for term in neutral_terms:
        if term in s:
            return "neutral"

    return "neutral"
