"""Business-type classifier for valuation routing.

This is intentionally a soft, rule-based v1: it returns a score for each
business archetype instead of forcing a brittle one-label decision. The top
label drives valuation-method routing, but prior snapshots can apply hysteresis
so a company does not flip methods because one quarter moved a threshold by a
few basis points.
"""
from __future__ import annotations

from typing import Dict, List, Optional


BUSINESS_TYPES = (
    "wide_moat_compounder",
    "profitable_growth",
    "high_growth_unprofitable",
    "platform_reinvestment_supercycle",
    "ai_accelerator_platform_leader",
    "mature_cash_flow",
    "cyclical",
    "financial",
    "asset_heavy",
    "other",
)

FINANCIAL_SECTORS = {"financial", "financials", "banks", "insurance"}
ASSET_HEAVY_SECTORS = {"energy", "utilities", "real estate", "materials", "industrials"}
CYCLICAL_SECTORS = {"energy", "materials", "industrials", "consumer cyclical", "autos"}

# Curated AI-accelerator / AI-capex supply-chain suppliers (NOT the capex
# *spenders* in platform_reinvestment_supercycle). These are picks-and-shovels
# names: high gross margin, high ROIC, high growth, but light capex intensity.
# Maintained by hand because "is an AI accelerator supplier" is not reliably
# derivable from yfinance fundamentals alone.
AI_ACCELERATOR_TICKERS = {"NVDA", "AVGO", "AMD", "TSM", "MU", "ARM"}


def _num(fundamentals: Dict, key: str) -> Optional[float]:
    v = fundamentals.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_sector(fundamentals: Dict, sector: Optional[str]) -> str:
    return str(sector or fundamentals.get("sector") or fundamentals.get("gics_sector") or "").strip().lower()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _ramp(x: Optional[float], lo: float, hi: float) -> float:
    if x is None or hi == lo:
        return 0.0
    return _clamp01((x - lo) / (hi - lo))


def _inverse_ramp(x: Optional[float], lo: float, hi: float) -> float:
    if x is None:
        return 0.0
    return 1.0 - _ramp(x, lo, hi)


def _missing_penalty(fundamentals: Dict, keys: List[str]) -> float:
    present = sum(1 for k in keys if _num(fundamentals, k) is not None)
    if not keys:
        return 1.0
    # Keep the classifier usable with sparse yfinance data, but lower confidence.
    return 0.55 + 0.45 * (present / len(keys))


def classify_business(
    fundamentals: Dict,
    *,
    sector: Optional[str] = None,
    prior_type: Optional[str] = None,
    hysteresis_margin: float = 0.15,
) -> Dict:
    """Return a soft business-type classification.

    Inputs are expected as ratios where possible (0.20 = 20%). ``market_cap`` is
    absolute dollars. Missing values lower confidence rather than forcing a type.
    """
    f = fundamentals or {}
    sec = _norm_sector(f, sector)
    market_cap = _num(f, "market_cap")
    revenue_growth = _num(f, "revenue_growth_yoy")
    gross_margin = _num(f, "gross_margin")
    operating_margin = _num(f, "operating_margin")
    fcf_margin = _num(f, "fcf_margin")
    net_margin = _num(f, "net_margin")
    roic = _num(f, "roic")
    debt_to_equity = _num(f, "debt_to_equity")
    revenue_volatility = _num(f, "revenue_volatility")
    capex_intensity = _num(f, "capex_intensity")
    capex_growth = _num(f, "capex_growth")
    ai_exposure = _num(f, "ai_exposure")  # 0..1, sourced from the curated AI-supercycle seed
    ticker = str(f.get("ticker") or "").strip().upper()

    scores = {t: 0.05 for t in BUSINESS_TYPES}
    reasons: List[str] = []

    if any(s in sec for s in FINANCIAL_SECTORS):
        scores["financial"] = 1.0
        reasons.append("sector indicates financial company; DCF/EV-EBITDA should not drive value")
    else:
        scores["financial"] = 0.0
        high_growth = _ramp(revenue_growth, 0.15, 0.35)
        profitability_gap = _inverse_ramp(fcf_margin, -0.05, 0.05)
        strong_margin = _ramp(gross_margin, 0.40, 0.70)
        fcf_positive = _ramp(fcf_margin, 0.00, 0.15)
        roic_quality = _ramp(roic, 0.08, 0.20)
        stable_growth = _inverse_ramp(revenue_volatility, 0.10, 0.35)
        low_growth = _inverse_ramp(revenue_growth, 0.06, 0.12)
        leverage_ok = _inverse_ramp(debt_to_equity, 1.0, 3.0)

        scores["high_growth_unprofitable"] = (
            0.40 * high_growth + 0.25 * profitability_gap + 0.20 * strong_margin + 0.15 * leverage_ok
        )
        scores["profitable_growth"] = (
            0.35 * high_growth + 0.25 * fcf_positive + 0.20 * strong_margin + 0.20 * roic_quality
        )
        large_cap = _ramp(market_cap, 50e9, 150e9)
        quality_margin = max(_ramp(fcf_margin, 0.10, 0.25), _ramp(operating_margin, 0.15, 0.35))
        scores["wide_moat_compounder"] = (
            0.25 * large_cap + 0.35 * roic_quality + 0.25 * quality_margin + 0.15 * stable_growth
        )
        scores["mature_cash_flow"] = (
            0.35 * low_growth + 0.35 * fcf_positive + 0.15 * stable_growth + 0.15 * leverage_ok
        )
        scores["cyclical"] = max(
            0.30 if any(s in sec for s in CYCLICAL_SECTORS) else 0.0,
            0.45 * _ramp(revenue_volatility, 0.15, 0.45)
            + 0.25 * _ramp(abs(operating_margin or 0.0), 0.00, 0.15)
            + 0.30 * _inverse_ramp(net_margin, 0.02, 0.10),
        )
        scores["asset_heavy"] = max(
            0.35 if any(s in sec for s in ASSET_HEAVY_SECTORS) else 0.0,
            0.60 * _ramp(capex_intensity, 0.08, 0.25) + 0.40 * _ramp(debt_to_equity, 1.0, 3.0),
        )
        # Platform reinvestment supercycle: large platforms ploughing record
        # capex into AI/datacenter capacity. Gated on AI exposure so capital-heavy
        # but non-AI businesses (utilities, telecoms) stay in asset_heavy/cyclical.
        capex_super = _ramp(capex_intensity, 0.10, 0.20)
        capex_accel = _ramp(capex_growth, 0.15, 0.40)
        platform_scale = _ramp(market_cap, 100e9, 500e9)
        ai_factor = _clamp01(ai_exposure if ai_exposure is not None else 0.0)
        scores["platform_reinvestment_supercycle"] = (
            0.30 * capex_super + 0.20 * capex_accel + 0.15 * platform_scale + 0.35 * ai_factor
        )

        # Definitional gate: a large platform with real AI exposure that is both
        # capex-intensive AND ramping capex is a reinvestment supercycle, not just
        # another profitable compounder. Dampen the look-alike archetypes so the
        # specialized type can win (mirrors how the financial sector is gated).
        supercycle_gate = (
            ai_factor > 0
            and capex_intensity is not None and capex_intensity > 0.12
            and capex_growth is not None and capex_growth > 0.20
        )
        if supercycle_gate:
            for t in ("profitable_growth", "wide_moat_compounder", "mature_cash_flow"):
                scores[t] *= 0.80
            reasons.append("AI/datacenter capex supercycle")

        # AI accelerator platform leader: picks-and-shovels supplier to the AI
        # capex cycle (NVDA/AVGO). High gross margin + high ROIC + high growth, but
        # LIGHT capex intensity (it is a supplier, not a spender) — which is exactly
        # why these names fall through to generic profitable_growth without a
        # dedicated archetype. Anchored on a curated supplier list because the
        # "AI accelerator" identity is not reliably inferable from fundamentals.
        ai_supplier_quality = (
            0.30 * _ramp(gross_margin, 0.55, 0.75)
            + 0.25 * _ramp(roic, 0.25, 0.50)
            + 0.25 * _ramp(revenue_growth, 0.20, 0.40)
            + 0.20 * _inverse_ramp(capex_intensity, 0.06, 0.18)
        )
        is_curated_accel = ticker in AI_ACCELERATOR_TICKERS
        scores["ai_accelerator_platform_leader"] = (
            (0.55 + 0.45 * ai_supplier_quality) if is_curated_accel else 0.0
        )
        if is_curated_accel:
            # Definitional gate: a curated AI accelerator supplier is this type, not
            # a generic compounder. Dampen the look-alikes so the specialized type
            # wins (mirrors the supercycle and financial gates).
            for t in ("profitable_growth", "wide_moat_compounder", "platform_reinvestment_supercycle"):
                scores[t] *= 0.80
            reasons.append("AI accelerator / capex supply-chain leader")
        if revenue_growth is not None and revenue_growth > 0.20:
            reasons.append("high revenue growth")
        if fcf_margin is not None and fcf_margin < 0:
            reasons.append("negative free-cash-flow margin")
        if roic is not None and roic > 0.15:
            reasons.append("high ROIC")
        if fcf_margin is not None and fcf_margin > 0.10:
            reasons.append("positive durable FCF margin")
        if any(s in sec for s in CYCLICAL_SECTORS):
            reasons.append("cyclical sector")

    scores = {k: round(_clamp01(v), 4) for k, v in scores.items()}
    sorted_types = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_type, top_score = sorted_types[0]
    selected = top_type
    if prior_type in scores and top_type != prior_type:
        if (top_score - scores[prior_type]) < hysteresis_margin:
            selected = prior_type
            reasons.append(f"hysteresis kept prior type {prior_type}")

    required = [
        "market_cap", "revenue_growth_yoy", "gross_margin", "operating_margin",
        "fcf_margin", "roic", "debt_to_equity",
    ]
    confidence = top_score * _missing_penalty(f, required)
    if not reasons:
        reasons.append("insufficient distinctive data; defaulted to nearest archetype")
    return {
        "business_type": selected,
        "type_scores": scores,
        "classification_confidence": round(_clamp01(confidence), 4),
        "classification_reason": reasons,
    }
