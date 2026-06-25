"""Unified DCF engine — the single canonical cash-flow valuation core.

This module is the shared substrate behind both DCF stacks:

* Decision Terminal (``backend/valuation_inputs.py``) — user-facing fair values.
* Brain nightly snapshot (``backend/brain/valuation*.py``) — intrinsic anchors.

It implements a Damodaran-style FCFF model where **growth must be paid for**:

    EBIT   = revenue * operating_margin
    NOPAT  = EBIT * (1 - tax_rate)
    reinvestment = (g / ROIC) * NOPAT          # the "growth costs capital" link
    FCFF   = NOPAT - reinvestment

Capex enters valuation **only** through reinvestment. We never both strip
growth capex out of FCF *and* credit the revenue it produces — that double
counts and is the single biggest overvaluation trap for AI-capex names.

The classic two-stage constant-growth DCF used by the Brain is just the special
case of :func:`discounted_value` with a flat growth path, so both stacks share
identical math with zero numeric drift.

Pure stdlib, deterministic, no I/O (except the optional AI-supercycle seed file).
Not investment advice.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── shared defaults ─────────────────────────────────────────────────────────
DEFAULT_TAX_RATE = 0.21
DEFAULT_EQUITY_PREMIUM = 0.05
DEFAULT_BETA = 1.0
KE_FLOOR_SPREAD = 0.02          # cost of equity floored at rf + 2%
KE_CAP = 0.16                   # raised from legacy 0.14 so high-risk names reach 11-15%
ROIC_FLOOR = 0.06               # avoid divide-by-tiny-ROIC reinvestment blow-ups
REINVESTMENT_RATE_CAP = 1.20    # a firm can out-invest current NOPAT, but not absurdly

# Execution-risk add-on (decimal) layered onto cost of equity by archetype.
EXECUTION_RISK_BY_TYPE = {
    "platform_reinvestment_supercycle": 0.015,
    "high_growth_unprofitable": 0.020,
    "profitable_growth": 0.010,
    "cyclical": 0.010,
    "wide_moat_compounder": 0.0,
    "mature_cash_flow": 0.0,
    "mature_stable": 0.0,
    "financial": 0.0,
    "asset_heavy": 0.005,
    "other": 0.005,
}

# Dynamic terminal growth band (base) by archetype, in decimal.
TERMINAL_GROWTH_BY_TYPE = {
    "platform_reinvestment_supercycle": 0.028,
    "wide_moat_compounder": 0.025,
    "profitable_growth": 0.025,
    "high_growth_unprofitable": 0.025,
    "mature_cash_flow": 0.020,
    "mature_stable": 0.020,
    "cyclical": 0.015,
    "asset_heavy": 0.015,
    "financial": 0.020,
    "other": 0.022,
}


def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


# ── core present-value math (generalizes brain.valuation.dcf_value) ──────────
def discounted_value(
    fcf0: float,
    growth_path: Sequence[float],
    terminal_growth: float,
    discount_rate: float,
) -> float:
    """Two-stage DCF present value of a cash-flow stream.

    Stage 1: explicit per-year growth from ``growth_path`` applied to ``fcf0``.
    Stage 2: Gordon terminal value on the final-year cash flow.

    With a flat ``growth_path`` of length ``years`` this is numerically identical
    to the legacy ``brain.valuation.dcf_value(fcf0, growth, years, ...)`` — that
    is how the two stacks stay in lockstep.
    """
    if discount_rate <= terminal_growth:
        raise ValueError("discount_rate must exceed terminal_growth")
    years = len(growth_path)
    if years <= 0:
        raise ValueError("growth_path must be non-empty")

    pv = 0.0
    fcf = float(fcf0)
    for t, g in enumerate(growth_path, start=1):
        fcf *= (1.0 + g)
        pv += fcf / (1.0 + discount_rate) ** t
    terminal = fcf * (1.0 + terminal_growth) / (discount_rate - terminal_growth)
    pv += terminal / (1.0 + discount_rate) ** years
    return float(pv)


def constant_growth_value(
    fcf0: float,
    growth: float,
    years: int,
    terminal_growth: float,
    discount_rate: float,
) -> float:
    """Flat-growth special case — the Brain's classic two-stage DCF."""
    if years <= 0:
        raise ValueError("years must be positive")
    return discounted_value(fcf0, [growth] * years, terminal_growth, discount_rate)


def multi_stage_path(
    anchor: float,
    terminal_growth: float,
    years: int,
    *,
    high_years: int = 3,
    fade_end_year: int = 7,
) -> List[float]:
    """Three-phase growth fade (replaces single linear decay).

    * Years 1..``high_years``: hold the anchor growth (competitive advantage).
    * Years ``high_years+1``..``fade_end_year``: linear competitive fade.
    * Years after ``fade_end_year``: linear convergence to ``terminal_growth``.

    All inputs/outputs are decimals (0.18 == 18%).
    """
    years = max(1, int(years))
    high_years = max(0, min(high_years, years))
    fade_end_year = max(high_years, min(fade_end_year, years))
    path: List[float] = []
    for t in range(1, years + 1):
        if t <= high_years:
            g = anchor
        elif t <= fade_end_year:
            span = max(1, fade_end_year - high_years)
            frac = (t - high_years) / span
            # fade roughly halfway toward terminal by the end of the fade window
            g = anchor + (terminal_growth - anchor) * 0.5 * frac
        else:
            span = max(1, years - fade_end_year)
            frac = (t - fade_end_year) / span
            mid = anchor + (terminal_growth - anchor) * 0.5
            g = mid + (terminal_growth - mid) * frac
        path.append(g)
    return path


# ── reverse DCF (one unknown at a time) ──────────────────────────────────────
def reverse_dcf_growth(
    target_value: float,
    fcf0: float,
    *,
    years: int = 5,
    terminal_growth: float = 0.025,
    discount_rate: float = 0.09,
    lo: float = -0.5,
    hi: float = 1.0,
    iters: int = 100,
) -> Optional[float]:
    """Solve for the constant growth rate the market price implies (bisection).

    ``discounted_value`` is monotincreasing in growth, so bisection converges.
    Returns the implied growth, or ``None`` if ``target_value`` is outside the
    reachable bracket.
    """
    def f(g: float) -> float:
        return constant_growth_value(fcf0, g, years, terminal_growth, discount_rate) - target_value

    f_lo, f_hi = f(lo), f(hi)
    if f_lo > 0 and f_hi > 0:
        return None
    if f_lo < 0 and f_hi < 0:
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if (f_mid > 0) == (f_hi > 0):
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _bisect(fn, target: float, lo: float, hi: float, iters: int = 100) -> Optional[float]:
    """Generic monotone bisection: find x s.t. fn(x) == target on [lo, hi]."""
    try:
        f_lo = fn(lo) - target
        f_hi = fn(hi) - target
    except (ValueError, ZeroDivisionError):
        return None
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        return None  # target not bracketed
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        try:
            f_mid = fn(mid) - target
        except (ValueError, ZeroDivisionError):
            return None
        if abs(f_mid) < 1e-9:
            return mid
        if (f_mid > 0) == (f_hi > 0):
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# ── cost of capital ──────────────────────────────────────────────────────────
def cost_of_equity(
    beta: Optional[float],
    *,
    risk_free: float,
    equity_premium: float = DEFAULT_EQUITY_PREMIUM,
    execution_risk: float = 0.0,
    cap: float = KE_CAP,
) -> float:
    """CAPM cost of equity plus an execution-risk add-on.

    ``Rf + beta*ERP + execution_risk``; floored at ``Rf + 2%`` and capped at
    ``cap`` (default 16%, up from the legacy 14% so high-risk names can reach the
    11-15% range institutional models use for AI builders / unprofitable growth).
    """
    b = _num(beta, DEFAULT_BETA) or DEFAULT_BETA
    ke = risk_free + b * equity_premium + max(0.0, execution_risk)
    return max(risk_free + KE_FLOOR_SPREAD, min(cap, ke))


def execution_risk_for(classification: str) -> float:
    return EXECUTION_RISK_BY_TYPE.get(classification, EXECUTION_RISK_BY_TYPE["other"])


def dynamic_terminal_growth(classification: str, risk_free: float) -> float:
    """Terminal growth by archetype, capped below the risk-free rate.

    Damodaran's rule of thumb: terminal growth cannot durably exceed the
    risk-free rate (a proxy for nominal economy growth), so we cap there.
    """
    base = TERMINAL_GROWTH_BY_TYPE.get(classification, TERMINAL_GROWTH_BY_TYPE["other"])
    return float(min(base, max(0.0, risk_free - 0.005)))


# ── FCFF model (growth tied to reinvestment / ROIC) ──────────────────────────
def fcff_series(
    revenue0: float,
    growth_path: Sequence[float],
    operating_margin_path: Sequence[float],
    *,
    tax_rate: float,
    roic: float,
    reinvestment_cap: float = REINVESTMENT_RATE_CAP,
) -> Tuple[List[float], float]:
    """Project an FCFF stream where reinvestment funds growth via ROIC.

    ``reinvestment = (g / ROIC) * NOPAT``; ``FCFF = NOPAT - reinvestment``. The
    reinvestment *rate* (g/ROIC) is clamped to ``[0, reinvestment_cap]`` so a
    high-growth year cannot imply a physically absurd reinvestment.

    Returns ``(fcff_list, ending_revenue)``.
    """
    roic_eff = max(ROIC_FLOOR, float(roic))
    revenue = float(revenue0)
    fcffs: List[float] = []
    n = len(growth_path)
    for i in range(n):
        g = growth_path[i]
        margin = operating_margin_path[i] if i < len(operating_margin_path) else operating_margin_path[-1]
        revenue *= (1.0 + g)
        nopat = revenue * margin * (1.0 - tax_rate)
        reinvest_rate = max(0.0, min(reinvestment_cap, g / roic_eff))
        reinvestment = reinvest_rate * nopat
        fcffs.append(nopat - reinvestment)
    return fcffs, revenue


def fcff_equity_value_per_share(
    *,
    revenue0: float,
    growth_path: Sequence[float],
    operating_margin_path: Sequence[float],
    tax_rate: float,
    roic: float,
    discount_rate: float,
    terminal_growth: float,
    net_cash: float,
    shares: float,
    reinvestment_cap: float = REINVESTMENT_RATE_CAP,
) -> Optional[float]:
    """Per-share equity value from an FCFF projection + Gordon terminal."""
    if shares is None or shares <= 0 or revenue0 is None or revenue0 <= 0:
        return None
    if discount_rate <= terminal_growth:
        return None
    fcffs, _ = fcff_series(
        revenue0, growth_path, operating_margin_path,
        tax_rate=tax_rate, roic=roic, reinvestment_cap=reinvestment_cap,
    )
    years = len(fcffs)
    pv = sum(fcffs[i] / (1.0 + discount_rate) ** (i + 1) for i in range(years))
    terminal_fcff = fcffs[-1] * (1.0 + terminal_growth)
    tv = terminal_fcff / (discount_rate - terminal_growth)
    pv += tv / (1.0 + discount_rate) ** years
    equity = pv + (net_cash or 0.0)
    return float(equity / shares)


def margin_path(
    current_margin: float,
    target_margin: float,
    years: int,
    *,
    margin_trend: float = 0.0,
    dip_depth: float = 0.0,
) -> List[float]:
    """Interpolate operating margin toward a target.

    Data-driven phase shape: we only model an early compression dip when the
    company's *observed* margin trend is negative (``margin_trend < 0``). Profitable
    hyperscalers with stable/expanding margins (NVDA/MSFT/GOOG today) get a clean
    glide to target with no fabricated compression.
    """
    years = max(1, int(years))
    apply_dip = margin_trend < 0 and dip_depth > 0
    path: List[float] = []
    trough_year = min(3, years)
    for t in range(1, years + 1):
        glide = current_margin + (target_margin - current_margin) * (t / years)
        if apply_dip and t <= trough_year:
            # V-shaped early dip then recovery back onto the glide path
            dip_frac = 1.0 - abs((t - trough_year / 2.0) / (trough_year / 2.0))
            glide -= dip_depth * max(0.0, dip_frac)
        path.append(max(0.0, glide))
    return path


# ── AI supercycle seed + valuation ───────────────────────────────────────────
_AI_SEED_CACHE: Optional[Dict[str, Any]] = None
_AI_SEED_PATH = os.path.join(os.path.dirname(__file__), "data", "ai_supercycle_seed.json")


def load_ai_supercycle_seed() -> Dict[str, Any]:
    """Load the curated AI-supercycle seed (segment revenue, sales-to-capital).

    There is no clean public API for per-segment AI/datacenter revenue and
    capex, so this is a hand-maintained file refreshed quarterly. Missing
    tickers fall back to the total-revenue FCFF path.
    """
    global _AI_SEED_CACHE
    if _AI_SEED_CACHE is not None:
        return _AI_SEED_CACHE
    try:
        with open(_AI_SEED_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            _AI_SEED_CACHE = data.get("tickers", data) if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.debug("[dcf_engine] AI supercycle seed unavailable: %s", exc)
        _AI_SEED_CACHE = {}
    return _AI_SEED_CACHE


def ai_supercycle_seed_for(ticker: str) -> Optional[Dict[str, Any]]:
    if not ticker:
        return None
    return load_ai_supercycle_seed().get(ticker.upper())


def _segmented_revenue_growth_path(
    core_growth: float,
    ai_growth: float,
    core_rev: float,
    ai_rev: float,
    years: int,
) -> Tuple[List[float], List[float], List[float], List[float]]:
    """Blend a decaying core segment and a faster-decaying AI segment.

    Returns ``(blended_growth_path, total_levels, core_levels, ai_levels)`` where
    the level lists have ``years+1`` entries (index 0 = starting values).
    """
    core = float(core_rev)
    ai = float(ai_rev)
    total_levels = [core + ai]
    core_levels = [core]
    ai_levels = [ai]
    blended: List[float] = []
    for t in range(1, years + 1):
        # AI growth fades faster than core toward a mature blended rate.
        ai_g = ai_growth * max(0.25, 1.0 - 0.06 * (t - 1))
        core_g = core_growth * max(0.5, 1.0 - 0.03 * (t - 1))
        core *= (1.0 + core_g)
        ai *= (1.0 + ai_g)
        new_total = core + ai
        prev_total = total_levels[-1]
        blended.append((new_total / prev_total) - 1.0 if prev_total > 0 else 0.0)
        total_levels.append(new_total)
        core_levels.append(core)
        ai_levels.append(ai)
    return blended, total_levels, core_levels, ai_levels


def supercycle_value_per_share(
    *,
    revenue0: float,
    seed: Dict[str, Any],
    operating_margin: float,
    tax_rate: float,
    roic: float,
    discount_rate: float,
    terminal_growth: float,
    net_cash: float,
    shares: float,
    years: int = 13,
    margin_trend: float = 0.0,
    margin_target: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Seed-backed AI capex-supercycle FCFF valuation.

    Reinvestment is tied to capital via ``sales_to_capital`` with a ``capex_lag``:
    capital is spent ``lag`` years *before* the revenue it generates, which
    correctly front-loads the AI-capex burden instead of hiding it. FCFF is still
    ``NOPAT - reinvestment`` so growth capex is never double-counted.
    """
    if shares is None or shares <= 0 or discount_rate <= terminal_growth:
        return None

    core_rev = _num(seed.get("core_revenue"))
    ai_rev = _num(seed.get("ai_revenue"))
    if core_rev is None or ai_rev is None:
        # Fall back to a single-segment split if only a fraction is given.
        ai_frac = _num(seed.get("ai_revenue_fraction"), 0.3) or 0.3
        rev = _num(revenue0) or 0.0
        ai_rev = rev * ai_frac
        core_rev = rev - ai_rev
    if (core_rev + ai_rev) <= 0:
        return None

    s2c = _num(seed.get("sales_to_capital"), 2.0) or 2.0
    lag = int(_num(seed.get("capex_lag_years"), 2) or 2)
    core_growth = _num(seed.get("core_growth"), 0.07) or 0.07
    ai_growth = _num(seed.get("ai_growth"), 0.30) or 0.30
    years = int(_num(seed.get("horizon_years"), years) or years)
    years = max(10, min(15, years))

    growth_path, levels, core_levels, ai_levels = _segmented_revenue_growth_path(
        core_growth, ai_growth, core_rev, ai_rev, years
    )

    # Segment-margin blending: if the seed encodes per-segment operating margins
    # (e.g. AWS/Azure cloud at ~35% vs retail at ~5%), build the operating-margin
    # path from the revenue mix each year. As the higher-margin AI/cloud segment
    # grows faster, the blended margin rises organically. This fixes names like
    # AMZN whose consolidated margin badly understates the cash-rich cloud segment.
    core_margin = _num(seed.get("core_margin"))
    ai_margin = _num(seed.get("ai_margin"))
    if core_margin is not None and ai_margin is not None:
        op_path = []
        for t in range(1, years + 1):
            tot = levels[t]
            blended_m = (
                (core_levels[t] * core_margin + ai_levels[t] * ai_margin) / tot
                if tot > 0 else operating_margin
            )
            op_path.append(max(0.0, blended_m))
    else:
        target = margin_target if margin_target is not None else max(operating_margin, operating_margin + 0.05)
        op_path = margin_path(
            operating_margin, target, years,
            margin_trend=margin_trend, dip_depth=0.03,
        )

    # Reinvestment funds growth contemporaneously (standard Damodaran
    # sales-to-capital): reinvestment_t = max(0, ΔRevenue_t) / sales_to_capital.
    # ``capex_lag_years`` scales reinvestment efficiency in the early ramp years
    # (capital leads revenue, so the first ``lag`` years reinvest a bit more per
    # dollar of realized growth) without the punitive forward-shift that overstated
    # the burden for very-large-revenue platforms.
    fcffs: List[float] = []
    for t in range(1, years + 1):
        revenue_t = levels[t]
        nopat = revenue_t * op_path[t - 1] * (1.0 - tax_rate)
        delta_rev = max(0.0, levels[t] - levels[t - 1])
        ramp_penalty = 1.0 + (0.10 * lag if t <= lag else 0.0)
        reinvestment = (delta_rev / s2c) * ramp_penalty
        fcffs.append(nopat - reinvestment)

    pv = sum(fcffs[i] / (1.0 + discount_rate) ** (i + 1) for i in range(years))
    terminal_fcff = fcffs[-1] * (1.0 + terminal_growth)
    tv = terminal_fcff / (discount_rate - terminal_growth)
    pv += tv / (1.0 + discount_rate) ** years
    equity = pv + (net_cash or 0.0)
    fair = float(equity / shares)
    tv_pv = (tv / (1.0 + discount_rate) ** years)
    return {
        "fair_value_per_share": fair,
        "years": years,
        "growth_path": [round(g, 4) for g in growth_path],
        "operating_margin_path": [round(m, 4) for m in op_path],
        "sales_to_capital": s2c,
        "capex_lag_years": lag,
        "terminal_value_pct": round((tv_pv / (pv)) * 100, 1) if pv > 0 else None,
    }


# ── maintenance vs growth capex (diagnostics only — never inflates FCF) ──────
def split_capex(
    *,
    capex: Optional[float],
    depreciation: Optional[float],
    avg_capex_5y: Optional[float] = None,
    stable_history: bool = False,
) -> Dict[str, Optional[float]]:
    """Estimate maintenance vs growth capex for diagnostics and risk flags.

    Per the plan this is **display only** — it is never used to build a
    "normalized FCF" that strips growth capex from the discounted stream, which
    would overstate value. Returns absolute (positive) dollar magnitudes.
    """
    capex_abs = abs(_num(capex, 0.0) or 0.0)
    if capex_abs <= 0:
        return {"maintenance_capex": None, "growth_capex": None, "source": "none"}

    dep = _num(depreciation)
    if dep is not None and dep > 0:
        maint = min(capex_abs, dep * 1.1)
        source = "depreciation_x1.1"
    elif stable_history and avg_capex_5y is not None:
        maint = min(capex_abs, abs(avg_capex_5y))
        source = "avg_5y_capex"
    else:
        maint = capex_abs * 0.4
        source = "capex_x0.4_fallback"
    growth = max(0.0, capex_abs - maint)
    return {"maintenance_capex": maint, "growth_capex": growth, "source": source}
