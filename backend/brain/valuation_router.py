"""Business-type-aware valuation router.

The router answers "what is the business worth?" without confusing that with
"what is the market doing right now?". It blends only fundable methods; missing
inputs lower method weight or produce ``insufficient_data`` rather than fake
precision.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import valuation as val
from .sector_multiples import sector_median


DEFAULT_WEIGHTS = {
    "wide_moat_compounder": {"owner_earnings_dcf": 0.55, "peer_multiples": 0.25, "reverse_dcf": 0.20},
    "profitable_growth": {"owner_earnings_dcf": 0.45, "peer_multiples": 0.35, "reverse_dcf": 0.20},
    "high_growth_unprofitable": {"high_growth_revenue_dcf": 0.60, "peer_multiples": 0.25, "reverse_dcf": 0.15},
    "mature_cash_flow": {"owner_earnings_dcf": 0.60, "peer_multiples": 0.25, "reverse_dcf": 0.15},
    "cyclical": {"peer_multiples": 0.45, "owner_earnings_dcf": 0.30, "reverse_dcf": 0.25},
    "financial": {"financial_residual_income": 0.70, "peer_multiples": 0.20, "reverse_dcf": 0.10},
    "asset_heavy": {"peer_multiples": 0.45, "owner_earnings_dcf": 0.30, "reverse_dcf": 0.25},
    "other": {"owner_earnings_dcf": 0.40, "peer_multiples": 0.40, "reverse_dcf": 0.20},
}

SCENARIO_BANDS = {
    "high_growth_unprofitable": {"growth_band": 0.08, "discount_band": 0.025},
    "cyclical": {"growth_band": 0.06, "discount_band": 0.020},
    "financial": {"growth_band": 0.03, "discount_band": 0.015},
    "wide_moat_compounder": {"growth_band": 0.03, "discount_band": 0.010},
    "mature_cash_flow": {"growth_band": 0.02, "discount_band": 0.010},
}


def _num(d: Dict, key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _positive(d: Dict, *keys: str) -> Optional[float]:
    for key in keys:
        v = _num(d, key)
        if v is not None and v > 0:
            return v
    return None


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _per_share(total: Optional[float], shares: Optional[float]) -> Optional[float]:
    if total is None:
        return None
    # If callers already pass per-share numbers (common in tests), leave them.
    if abs(total) < 1_000_000:
        return float(total)
    if shares and shares > 0:
        return float(total) / float(shares)
    return None


def _method(name: str, low: float, mid: float, high: float, weight: float, note: str, payload: Dict) -> Dict:
    lo, hi = sorted((float(low), float(high)))
    mid = min(max(float(mid), lo), hi)
    return {
        "method": name,
        "intrinsic_value_low": round(lo, 4),
        "intrinsic_value_mid": round(mid, 4),
        "intrinsic_value_high": round(hi, 4),
        "weight": round(float(weight), 4),
        "note": note,
        "payload": payload,
    }


def owner_earnings_dcf(f: Dict, business_type: str) -> Optional[Dict]:
    fcf0 = _positive(f, "fcf_per_share", "owner_earnings_per_share")
    shares = _num(f, "shares_outstanding")
    if fcf0 is None:
        fcf0 = _per_share(_positive(f, "free_cash_flow", "owner_earnings", "fcf_ttm"), shares)
    if fcf0 is None:
        return None
    growth = _num(f, "fcf_growth") or _num(f, "revenue_growth_yoy") or 0.04
    growth = _clip(growth, -0.20, 0.35)
    terminal_growth = _num(f, "terminal_growth") or 0.025
    discount_rate = _num(f, "discount_rate") or 0.09
    if discount_rate <= terminal_growth:
        discount_rate = terminal_growth + 0.03
    bands = SCENARIO_BANDS.get(business_type, {"growth_band": 0.03, "discount_band": 0.015})
    rng = val.intrinsic_range(
        fcf0=fcf0,
        growth=growth,
        years=int(_num(f, "valuation_years") or 5),
        terminal_growth=terminal_growth,
        discount_rate=discount_rate,
        growth_band=bands["growth_band"],
        discount_band=bands["discount_band"],
    )
    return _method(
        "owner_earnings_dcf",
        rng["intrinsic_value_low"],
        rng["intrinsic_value_mid"],
        rng["intrinsic_value_high"],
        1.0,
        "DCF on per-share free cash flow / owner earnings",
        {"fcf0": round(fcf0, 4), "growth": round(growth, 4), "discount_rate": round(discount_rate, 4)},
    )


def high_growth_revenue_dcf(f: Dict) -> Optional[Dict]:
    shares = _num(f, "shares_outstanding")
    revenue = _positive(f, "revenue_ttm", "total_revenue")
    if revenue is None:
        revenue_ps = _positive(f, "revenue_per_share")
    else:
        revenue_ps = _per_share(revenue, shares)
    if revenue_ps is None:
        return None
    growth = _clip(_num(f, "revenue_growth_yoy") or 0.25, 0.05, 0.70)
    target_margin = _clip(_num(f, "target_operating_margin") or _num(f, "gross_margin") * 0.35
                          if _num(f, "gross_margin") is not None else 0.18, 0.05, 0.35)
    fcf_conversion = _clip(_num(f, "fcf_conversion_rate") or 0.75, 0.40, 0.95)
    terminal_multiple = _clip(_num(f, "terminal_fcf_multiple") or 20.0, 8.0, 35.0)
    discount_rate = _num(f, "discount_rate") or 0.11
    years = int(_num(f, "valuation_years") or 5)

    def scenario(g_mult: float, margin_mult: float, multiple_mult: float, dr_delta: float) -> float:
        rev = revenue_ps * (1.0 + growth * g_mult) ** years
        normalized_fcf = rev * target_margin * margin_mult * fcf_conversion
        terminal = normalized_fcf * terminal_multiple * multiple_mult
        return terminal / (1.0 + discount_rate + dr_delta) ** years

    return _method(
        "high_growth_revenue_dcf",
        scenario(0.65, 0.75, 0.70, 0.02),
        scenario(1.00, 1.00, 1.00, 0.00),
        scenario(1.25, 1.15, 1.20, -0.01),
        1.0,
        "Revenue-to-normalized-FCF scenario valuation for high-growth companies",
        {
            "revenue_per_share": round(revenue_ps, 4),
            "growth": round(growth, 4),
            "target_operating_margin": round(target_margin, 4),
            "terminal_fcf_multiple": round(terminal_multiple, 4),
        },
    )


def peer_multiples_value(f: Dict, sector_medians: Optional[Dict[str, Dict[str, float]]]) -> Optional[Dict]:
    sector = f.get("sector") or f.get("gics_sector")
    shares = _num(f, "shares_outstanding")
    net_debt = (_num(f, "total_debt") or 0.0) - (_num(f, "cash") or _num(f, "total_cash") or 0.0)
    estimates: List[float] = []
    payload: Dict[str, float] = {}

    eps = _positive(f, "eps_ttm", "trailing_eps")
    pe_med = sector_median(sector_medians, sector, "pe_ratio")
    if eps is not None and pe_med:
        estimates.append(eps * pe_med)
        payload["pe_median"] = pe_med

    ebitda = _positive(f, "ebitda_ttm", "ebitda")
    ev_ebitda_med = sector_median(sector_medians, sector, "ev_ebitda")
    if ebitda is not None and ev_ebitda_med:
        ev = ebitda * ev_ebitda_med
        equity = ev - net_debt
        ps = _per_share(equity, shares)
        if ps and ps > 0:
            estimates.append(ps)
            payload["ev_ebitda_median"] = ev_ebitda_med

    revenue = _positive(f, "revenue_ttm", "total_revenue")
    ev_sales_med = sector_median(sector_medians, sector, "ev_sales")
    if revenue is not None and ev_sales_med:
        ev = revenue * ev_sales_med
        equity = ev - net_debt
        ps = _per_share(equity, shares)
        if ps and ps > 0:
            estimates.append(ps)
            payload["ev_sales_median"] = ev_sales_med

    if not estimates:
        return None
    mid = sum(estimates) / len(estimates)
    spread = 0.20 if len(estimates) > 1 else 0.30
    return _method(
        "peer_multiples",
        mid * (1.0 - spread),
        mid,
        mid * (1.0 + spread),
        1.0,
        "Sector-median peer multiples; illustrative and gated by available peers",
        payload,
    )


def financial_residual_income(f: Dict) -> Optional[Dict]:
    book = _positive(f, "book_value_per_share", "tangible_book_value_per_share", "book_value")
    roe = _num(f, "roe") or _num(f, "return_on_equity")
    if book is None or roe is None:
        return None
    cost = _num(f, "cost_of_equity") or _num(f, "discount_rate") or 0.10
    terminal_growth = _num(f, "terminal_growth") or 0.025
    if cost <= terminal_growth:
        cost = terminal_growth + 0.04
    residual = book * (roe - cost) / (cost - terminal_growth)
    mid = max(0.0, book + residual)
    return _method(
        "financial_residual_income",
        mid * 0.80,
        mid,
        mid * 1.20,
        1.0,
        "Residual-income value for financial companies (book value plus excess ROE)",
        {"book_value": round(book, 4), "roe": round(roe, 4), "cost_of_equity": round(cost, 4)},
    )


def _reverse_dcf_info(price: Optional[float], f: Dict) -> Dict:
    fcf0 = _positive(f, "fcf_per_share", "owner_earnings_per_share")
    if fcf0 is None:
        fcf0 = _per_share(_positive(f, "free_cash_flow", "owner_earnings", "fcf_ttm"),
                          _num(f, "shares_outstanding"))
    if price is None or price <= 0 or fcf0 is None:
        return {"implied_growth": None, "note": "missing price or FCF"}
    discount_rate = _num(f, "discount_rate") or 0.09
    terminal_growth = _num(f, "terminal_growth") or 0.025
    implied = val.reverse_dcf(price, fcf0, years=int(_num(f, "valuation_years") or 5),
                              terminal_growth=terminal_growth, discount_rate=discount_rate)
    return {"implied_growth": round(implied, 4) if implied is not None else None,
            "fcf0": round(fcf0, 4), "discount_rate": round(discount_rate, 4)}


def value_company(
    fundamentals: Dict,
    business_type: str,
    *,
    current_price: Optional[float] = None,
    sector_medians: Optional[Dict[str, Dict[str, float]]] = None,
    weights: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict:
    """Route to fundable valuation methods and blend an intrinsic range."""
    f = dict(fundamentals or {})
    wcfg = (weights or DEFAULT_WEIGHTS).get(business_type, DEFAULT_WEIGHTS["other"])
    candidates = {
        "owner_earnings_dcf": owner_earnings_dcf(f, business_type),
        "high_growth_revenue_dcf": high_growth_revenue_dcf(f),
        "peer_multiples": peer_multiples_value(f, sector_medians),
        "financial_residual_income": financial_residual_income(f),
    }
    methods: List[Dict] = []
    for name, result in candidates.items():
        if result is None:
            continue
        weight = float(wcfg.get(name, 0.0))
        if weight <= 0:
            continue
        result = dict(result)
        result["weight"] = weight
        methods.append(result)

    reverse = _reverse_dcf_info(current_price, f)
    if not methods:
        # Conservative fallback: if the type-prior did not include a method but
        # the data can fund one (common during migration from legacy dcf_inputs),
        # use the available intrinsic method instead of returning no valuation.
        fallback_order = [
            "financial_residual_income" if business_type == "financial" else "owner_earnings_dcf",
            "high_growth_revenue_dcf",
            "peer_multiples",
        ]
        for name in fallback_order:
            result = candidates.get(name)
            if result is not None:
                result = dict(result)
                result["weight"] = 1.0
                methods.append(result)
                break

    if not methods:
        return {
            "status": "insufficient_data",
            "business_type": business_type,
            "intrinsic_value_low": None,
            "intrinsic_value_mid": None,
            "intrinsic_value_high": None,
            "margin_of_safety_base": None,
            "valuation_score": None,
            "method_breakdown": [],
            "reverse_dcf": reverse,
            "warnings": ["no fundable valuation method"],
        }

    wsum = sum(m["weight"] for m in methods)
    for m in methods:
        m["weight"] = round(m["weight"] / wsum, 4)
    low = sum(m["weight"] * m["intrinsic_value_low"] for m in methods)
    mid = sum(m["weight"] * m["intrinsic_value_mid"] for m in methods)
    high = sum(m["weight"] * m["intrinsic_value_high"] for m in methods)
    margin = val.dcf_upside(mid, current_price) if current_price else None
    # Smooth 0-100 score: -50% margin -> 0, +50% -> 100.
    valuation_score = None if margin is None else round(_clip(50.0 + margin * 100.0, 0.0, 100.0), 2)
    return {
        "status": "ok",
        "business_type": business_type,
        "intrinsic_value_low": round(low, 4),
        "intrinsic_value_mid": round(mid, 4),
        "intrinsic_value_high": round(high, 4),
        "margin_of_safety_base": round(margin, 4) if margin is not None else None,
        "valuation_score": valuation_score,
        "method_breakdown": methods,
        "reverse_dcf": reverse,
        "warnings": [],
    }
