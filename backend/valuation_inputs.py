"""
Unified valuation inputs for owner-earnings DCF (yfinance + statement fallbacks).

Used by decision_terminal valuation models — not investment advice.
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple
from backend import dcf_engine
from backend.brain.business_classifier import classify_business

logger = logging.getLogger(__name__)

_OCF_LABELS = ("Operating Cash Flow",)
_CAPEX_LABELS = ("Capital Expenditure", "Purchase Of PPE")
MIN_ANNUAL_OCF_YEARS = 3
MAX_ANNUAL_OCF_YEARS = 5

DEFAULT_RISK_FREE = 0.0446
DEFAULT_EQUITY_PREMIUM = 0.05
DEFAULT_BETA = 1.0
DEFAULT_TAX_RATE = 0.21
DEFAULT_COST_OF_DEBT_SPREAD = 0.015
DCF_YEARS = 5

BASE_GROWTH_PATH = [0.06, 0.05, 0.04, 0.035, 0.03]
BEAR_GROWTH_PATH = [0.02, 0.02, 0.02, 0.02, 0.02]
BULL_GROWTH_PATH = [0.10, 0.09, 0.08, 0.07, 0.05]

BASE_TERMINAL_G = 0.025
BEAR_TERMINAL_G = 0.02
BULL_TERMINAL_G = 0.03


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


def _first_row_value(df: Any, labels: Tuple[str, ...]) -> Optional[float]:
    if df is None or getattr(df, "empty", True):
        return None
    for label in labels:
        if label in df.index:
            val = _num(df.loc[label].iloc[0])
            if val is not None:
                return val
    return None


def _owner_earnings(ocf: float, capex: Optional[float]) -> float:
    if capex is not None:
        return ocf + capex if capex <= 0 else ocf - abs(capex)
    return ocf


def _annual_cashflow_rows(cashflow_df: Any, *, max_years: int = MAX_ANNUAL_OCF_YEARS) -> List[Dict[str, Any]]:
    """Extract up to ``max_years`` of annual OCF/capex, sorted oldest → newest."""
    if cashflow_df is None or getattr(cashflow_df, "empty", True):
        return []

    ocf_series = None
    for label in _OCF_LABELS:
        if label in cashflow_df.index:
            ocf_series = cashflow_df.loc[label]
            break
    if ocf_series is None:
        return []

    capex_series = None
    for label in _CAPEX_LABELS:
        if label in cashflow_df.index:
            capex_series = cashflow_df.loc[label]
            break

    rows: List[Dict[str, Any]] = []
    for col in ocf_series.index:
        ocf = _num(ocf_series[col])
        if ocf is None:
            continue
        capex = None
        if capex_series is not None and col in capex_series.index:
            capex = _num(capex_series[col])
        try:
            year = int(col.year) if hasattr(col, "year") else int(str(col)[:4])
        except (TypeError, ValueError):
            continue
        rows.append({"year": year, "ocf": ocf, "capex": capex})

    rows.sort(key=lambda r: r["year"])
    if len(rows) > max_years:
        rows = rows[-max_years:]
    return rows


def median_owner_earnings_fcf(rows: List[Dict[str, Any]]) -> Tuple[Optional[float], str]:
    """
    Weighted normalized FCF: 50% latest + 30% 3y avg + 20% 5y median (if >=5 years available).
    If <5 years, falls back entirely to the latest FCF.
    Includes negative FCF years.
    """
    earnings: List[float] = []
    for row in rows:
        ocf = _num(row.get("ocf"))
        if ocf is None:
            continue
        oe = _owner_earnings(ocf, _num(row.get("capex")))
        earnings.append(oe)

    if not earnings:
        return None, "none"

    latest_fcf = earnings[-1]

    if len(earnings) >= 5:
        avg_3y_fcf = sum(earnings[-3:]) / 3.0
        median_5y_fcf = float(statistics.median(earnings[-5:]))
        normalized = 0.50 * latest_fcf + 0.30 * avg_3y_fcf + 0.20 * median_5y_fcf
        return normalized, "weighted_normalized_fcf"

    return latest_fcf, "latest_fcf_fallback"


def median_ocf_yoy_growth_pct(rows: List[Dict[str, Any]]) -> Optional[float]:
    """Median consecutive YoY OCF growth (%), oldest → newest."""
    ordered = sorted(rows, key=lambda r: r["year"])
    rates: List[float] = []
    for i in range(1, len(ordered)):
        prev = _num(ordered[i - 1].get("ocf"))
        cur = _num(ordered[i].get("ocf"))
        if prev is None or cur is None or prev <= 0:
            continue
        rates.append((cur / prev - 1.0) * 100.0)
    if not rates:
        return None
    return round(float(statistics.median(rates)), 2)


def _capex_growth_5y(capex_history: Optional[List[Any]]) -> Optional[float]:
    """CAGR of capex magnitude across available fiscal years (decimal).

    Used by the classifier to detect a reinvestment supercycle (capex ramping
    faster than revenue). Returns None when there is not enough history.
    """
    vals = [abs(v) for v in (capex_history or []) if _num(v) is not None and abs(_num(v)) > 0]
    if len(vals) < 2:
        return None
    first, last = vals[0], vals[-1]
    n = len(vals) - 1
    if first <= 0:
        return None
    try:
        return float((last / first) ** (1.0 / n) - 1.0)
    except (ValueError, ZeroDivisionError):
        return None


def risk_free_rate() -> float:
    """10Y Treasury as decimal; seed file or default (~4.46%)."""
    try:
        from backend.connectors.fred import _load_fred_seed

        seed = _load_fred_seed() or {}
        t10 = _num(seed.get("treasury_10y"))
        if t10 is not None and t10 > 0:
            return t10 / 100.0 if t10 > 1.0 else t10
    except Exception as exc:
        logger.debug("[valuation_inputs] risk_free_rate seed read failed: %s", exc)
    return DEFAULT_RISK_FREE


def capm_wacc(
    beta: Optional[float],
    *,
    risk_free: Optional[float] = None,
    equity_premium: float = DEFAULT_EQUITY_PREMIUM,
) -> float:
    """Simple CAPM cost of equity: Rf + beta * ERP, floored at Rf + 2%."""
    rf = risk_free if risk_free is not None else risk_free_rate()
    b = _num(beta, DEFAULT_BETA) or DEFAULT_BETA
    ke = rf + b * equity_premium
    return max(rf + 0.02, min(0.14, ke))


def compute_true_wacc(
    ke: float,
    market_cap: float,
    total_debt: float,
    risk_free: float,
) -> float:
    """Computes full WACC combining cost of equity and after-tax cost of debt."""
    if market_cap <= 0:
        return ke

    total_capital = market_cap + total_debt
    equity_weight = market_cap / total_capital
    debt_weight = total_debt / total_capital

    kd = risk_free + DEFAULT_COST_OF_DEBT_SPREAD
    kd_after_tax = kd * (1.0 - DEFAULT_TAX_RATE)

    return equity_weight * ke + debt_weight * kd_after_tax


def owner_earnings_fcf(snapshot: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """
    Prefer OCF − |capex|; then statement Free Cash Flow; then .info freeCashflow.
    Returns (fcf_usd, source_label).
    """
    ocf = _num(snapshot.get("operatingCashflow"))
    capex = _num(snapshot.get("capitalExpenditures"))
    if ocf is not None and capex is not None:
        # yfinance capex is usually negative
        return ocf + capex if capex <= 0 else ocf - abs(capex), "ocf_minus_capex"

    stmt_fcf = _num(snapshot.get("statement_free_cash_flow"))
    if stmt_fcf is not None and stmt_fcf > 0:
        return stmt_fcf, "cashflow_statement_fcf"

    info_fcf = _num(snapshot.get("freeCashflow"))
    if info_fcf is not None and info_fcf > 0:
        return info_fcf, "yfinance_freeCashflow"

    return None, "none"


def net_cash_equity(snapshot: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """
    Cash + short-term + long-term investments − total debt.
    Subtracts an estimate of required operating cash (e.g. 3% of revenue).
    Uses balance-sheet fallbacks when .info fields are missing.
    """
    sector = snapshot.get("sector")
    if sector in ["Financial Services", "Financials", "Banks", "Insurance", "Capital Markets"]:
        return None, "sector_excluded"

    cash = _num(snapshot.get("totalCash")) or 0.0
    st_inv = _num(snapshot.get("shortTermInvestments")) or 0.0
    lt_inv = _num(snapshot.get("longTermInvestments")) or 0.0
    debt = _num(snapshot.get("totalDebt")) or 0.0

    bs_cash_st = _num(snapshot.get("balance_cash_and_st_investments"))
    bs_inv = _num(snapshot.get("balance_investments_and_advances"))
    bs_debt = _num(snapshot.get("balance_total_debt"))

    if bs_cash_st is not None:
        cash = bs_cash_st
        st_inv = 0.0
    if bs_inv is not None:
        lt_inv = bs_inv
    if bs_debt is not None:
        debt = bs_debt

    if cash == 0 and st_inv == 0 and lt_inv == 0 and debt == 0:
        return None, "none"

    # Subtract estimated required operating cash (3% of revenue)
    rev = _num(snapshot.get("totalRevenue")) or 0.0
    required_operating_cash = rev * 0.03

    net = cash + st_inv + lt_inv - debt - required_operating_cash
    src = "balance_sheet" if bs_cash_st is not None or bs_inv is not None else "yfinance_info"
    return net, src


def dcf_equity_value(
    fcf_start: float,
    growth_path: List[float],
    wacc: float,
    terminal_growth: float,
    *,
    years: int = DCF_YEARS,
) -> float:
    """Gordon-style terminal value after explicit growth path."""
    if fcf_start <= 0 or wacc <= terminal_growth:
        raise ValueError("invalid DCF inputs")

    path = list(growth_path[:years])
    while len(path) < years:
        path.append(path[-1] if path else 0.03)

    fcfs = [fcf_start]
    for g in path:
        fcfs.append(fcfs[-1] * (1.0 + g))

    pv = sum(fcfs[i] / ((1.0 + wacc) ** i) for i in range(1, years + 1))
    terminal_fcf = fcfs[years] * (1.0 + terminal_growth)
    tv = terminal_fcf / (wacc - terminal_growth)
    pv += tv / ((1.0 + wacc) ** years)
    return pv


def dcf_fair_value_per_share(
    fcf_start: float,
    shares: float,
    net_cash: float,
    growth_path: List[float],
    wacc: float,
    terminal_growth: float,
) -> Optional[float]:
    if shares <= 0:
        return None
    try:
        equity = dcf_equity_value(fcf_start, growth_path, wacc, terminal_growth) + net_cash
        return float(equity / shares)
    except (ValueError, ZeroDivisionError):
        return None


def calculate_blended_growth_anchor(
    fcf_cagr: Optional[float],
    revenue_growth: Optional[float],
    ocf_cagr: Optional[float],
    forward_growth_estimate: Optional[float] = None,
) -> float:
    """
    Blended growth anchor (percent units):
    40% FCF CAGR + 25% revenue CAGR + 20% OCF CAGR + 15% forward estimate.

    Weights sum to 1.0. When a component is missing the remaining weights are
    renormalized so the anchor is always a proper convex blend of the metrics we
    actually have. (The legacy version declared 35/30/15 = 80%; the missing 20%
    was a forward-looking analyst estimate that is now wired in here.)
    """
    components = []
    if fcf_cagr is not None:
        components.append((fcf_cagr, 0.40))
    if revenue_growth is not None:
        # Convert revenue growth to percentage if it's not already
        rg = revenue_growth * 100.0 if abs(revenue_growth) < 1.0 else revenue_growth
        components.append((rg, 0.25))
    if ocf_cagr is not None:
        components.append((ocf_cagr, 0.20))
    if forward_growth_estimate is not None:
        fg = (
            forward_growth_estimate * 100.0
            if abs(forward_growth_estimate) < 1.0
            else forward_growth_estimate
        )
        components.append((fg, 0.15))

    if not components:
        return 0.05 * 100.0 # Default 5%

    total_weight = sum(w for _, w in components)
    blended_cagr = sum(val * (w / total_weight) for val, w in components)
    return blended_cagr


def build_base_growth_path(
    anchor_pct: float,
    terminal_growth: float = BASE_TERMINAL_G,
    business_type: str = "other",
) -> List[float]:
    """
    Maps the anchor into a gradual 5-year path.
    g1 = anchor
    g2 = 0.80 * anchor + 0.20 * terminal
    g3 = 0.60 * anchor + 0.40 * terminal
    g4 = 0.40 * anchor + 0.60 * terminal
    g5 = 0.25 * anchor + 0.75 * terminal
    """
    anchor = anchor_pct / 100.0
    if business_type in ("profitable_growth", "high_growth_unprofitable", "wide_moat_compounder"):
        max_g = 0.35
    else:
        max_g = 0.15
    anchor = max(0.02, min(max_g, anchor)) # Clamp between 2% and dynamic max growth (15% or 35%)

    return [
        anchor,
        0.80 * anchor + 0.20 * terminal_growth,
        0.60 * anchor + 0.40 * terminal_growth,
        0.40 * anchor + 0.60 * terminal_growth,
        0.25 * anchor + 0.75 * terminal_growth,
    ]


def _capex_split_diagnostics(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Maintenance vs growth capex for display + risk flags (never inflates FCF)."""
    capex = _num(snapshot.get("capitalExpenditures"))
    dep = _num(snapshot.get("depreciation"))
    hist = [abs(v) for v in (snapshot.get("capex_history_5y") or []) if _num(v) is not None]
    avg_capex_5y = (sum(hist) / len(hist)) if hist else None
    stable = len(hist) >= 4
    return dcf_engine.split_capex(
        capex=capex,
        depreciation=dep,
        avg_capex_5y=avg_capex_5y,
        stable_history=stable,
    )


def _market_expectation_label(margin_of_safety_pct: Optional[float]) -> str:
    if margin_of_safety_pct is None:
        return "unknown"
    if margin_of_safety_pct <= -25:
        return "high optimism priced in"
    if margin_of_safety_pct < -8:
        return "above base case"
    if margin_of_safety_pct <= 8:
        return "near base case"
    if margin_of_safety_pct <= 25:
        return "below base case"
    return "deep value vs base case"


def _enrich_valuation_output(
    out: Dict[str, Any],
    *,
    snapshot: Dict[str, Any],
    classification: Dict[str, Any],
    price_usd: Optional[float],
    fcf_per_share: Optional[float],
    wacc: float,
    terminal_growth: float,
    years: int,
) -> Dict[str, Any]:
    """Attach classification, reverse-DCF implied growth, a market-implied scenario,
    capex diagnostics, valuation range, margin of safety and risk flags.

    Shared by the owner-earnings and supercycle paths so every surface emits the
    same V2 fields.
    """
    out["classification"] = {
        "business_type": classification.get("business_type"),
        "confidence": classification.get("classification_confidence"),
        "reasons": classification.get("classification_reason", []),
        "type_scores": classification.get("type_scores", {}),
    }

    scenarios = out.get("scenarios") or {}
    bear = scenarios.get("bear")
    base = scenarios.get("base")
    bull = scenarios.get("bull")
    rng = [v for v in (bear, bull) if v is not None]
    if rng:
        out["valuation_range"] = [round(min(rng), 2), round(max(rng), 2)]

    margin_of_safety = None
    if base is not None and price_usd and base > 0:
        margin_of_safety = round((base - price_usd) / base * 100.0, 1)
    out["margin_of_safety_pct"] = margin_of_safety
    out["market_expectation"] = _market_expectation_label(margin_of_safety)

    # Reverse DCF: implied constant growth the current price embeds (one unknown).
    implied_growth = None
    if price_usd and price_usd > 0 and fcf_per_share and fcf_per_share > 0:
        net_cash = _num(out.get("net_cash_usd"), 0.0) or 0.0
        shares = _num(out.get("shares")) or 0.0
        net_cash_ps = (net_cash / shares) if shares > 0 else 0.0
        target_dcf_ps = price_usd - net_cash_ps
        if target_dcf_ps > 0 and wacc > terminal_growth:
            implied_growth = dcf_engine.reverse_dcf_growth(
                target_dcf_ps,
                fcf_per_share,
                years=years,
                terminal_growth=terminal_growth,
                discount_rate=wacc,
            )
    out["implied_growth"] = round(implied_growth, 4) if implied_growth is not None else None

    if price_usd and price_usd > 0:
        scenarios = dict(scenarios)
        scenarios["market_implied"] = round(float(price_usd), 2)
        out["scenarios"] = scenarios

    # Capex split (diagnostics only) + capex-inefficiency risk flag.
    capex_split = _capex_split_diagnostics(snapshot)
    out["capex_split"] = capex_split
    flags = list(out.get("valuation_warning_flags", []))
    growth_capex = capex_split.get("growth_capex")
    roic = _num(snapshot.get("returnOnEquity"))
    capex_growth = _capex_growth_5y(snapshot.get("capex_history_5y"))
    if (
        growth_capex
        and capex_growth is not None
        and capex_growth > 0.20
        and roic is not None
        and roic < 0.10
    ):
        flags.append("capex_inefficiency")
    out["valuation_warning_flags"] = flags
    out["risk_flags"] = flags
    return out


def compute_dcf_scenarios(
    snapshot: Dict[str, Any],
    *,
    hist_cagr_pct: Optional[float] = None,
    price_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Bear / base / bull owner-earnings DCF fair values per share.
    Returns dict with fair_value base + scenarios + provenance inputs.
    Includes a model selector to route to High-Growth Revenue-to-FCF DCF if needed.
    """
    # Model Selection Logic
    rev_g = _num(snapshot.get("revenueGrowth"))
    gross_margin = _num(snapshot.get("grossMargins"))

    annual_rows = snapshot.get("annual_cashflow_5y") or []
    fcf, _ = owner_earnings_fcf(snapshot)
    rev_0 = _num(snapshot.get("totalRevenue"))

    current_fcf_margin = None
    if fcf is not None and rev_0 is not None and rev_0 > 0:
        current_fcf_margin = fcf / rev_0

    positive_fcf_years = sum(1 for r in annual_rows if _num(r.get("ocf")) is not None and _num(r.get("ocf")) > 0)

    # Run the soft business classifier to determine business type
    fcf_margin_val = current_fcf_margin if current_fcf_margin is not None else 0.0
    gross_margin_val = gross_margin or 0.0
    operating_margin_val = _num(snapshot.get("operatingMargins")) or ((_num(snapshot.get("operatingIncome")) or 0.0) / rev_0 if rev_0 else 0.0)
    market_cap_val = _num(snapshot.get("marketCap")) or 0.0
    revenue_growth_yoy_val = rev_g or 0.0
    roe_val = _num(snapshot.get("returnOnEquity")) or 0.0
    debt_to_equity_val = (_num(snapshot.get("debtToEquity")) / 100.0) if snapshot.get("debtToEquity") else 0.0
    capex_val = _num(snapshot.get("capitalExpenditures")) or 0.0
    capex_intensity_val = (abs(capex_val) / rev_0) if (capex_val and rev_0 and rev_0 > 0) else 0.0

    capex_growth_val = _capex_growth_5y(snapshot.get("capex_history_5y"))
    ai_seed = dcf_engine.ai_supercycle_seed_for(snapshot.get("ticker") or "")
    ai_exposure_val = 1.0 if ai_seed else 0.0

    fundamentals_dict = {
        "market_cap": market_cap_val,
        "revenue_growth_yoy": revenue_growth_yoy_val,
        "gross_margin": gross_margin_val,
        "operating_margin": operating_margin_val,
        "fcf_margin": fcf_margin_val,
        "roic": roe_val,  # ROIC proxy
        "debt_to_equity": debt_to_equity_val,
        "capex_intensity": capex_intensity_val,
        "capex_growth": capex_growth_val,
        "ai_exposure": ai_exposure_val,
        "sector": snapshot.get("sector"),
    }
    classification = classify_business(fundamentals_dict)
    business_type = classification["business_type"]

    # Route AI capex-supercycle platforms to the FCFF segment engine first.
    if business_type == "platform_reinvestment_supercycle" and ai_seed:
        res = compute_supercycle_dcf_scenarios(
            snapshot,
            seed=ai_seed,
            classification=classification,
            price_usd=price_usd,
        )
        if res.get("available"):
            return res

    # Route based on classified business type
    if business_type in ("high_growth_unprofitable", "profitable_growth"):
        model_type = "High-Growth"
    elif current_fcf_margin is not None and current_fcf_margin > 0.05 and positive_fcf_years >= 3:
        model_type = "Owner-Earnings"
    elif rev_g is not None and rev_g > 0.20 and gross_margin is not None and gross_margin > 0.40:
        model_type = "High-Growth"
    else:
        model_type = "Owner-Earnings" # Fallback to standard owner-earnings DCF

    if model_type == "High-Growth":
        res = compute_high_growth_dcf_scenarios(snapshot, price_usd=price_usd, business_type=business_type)
        if res.get("available"):
            res["model_name"] = "High-Growth Revenue-to-FCF DCF"
            return res

    # Standard Owner-Earnings execution continues here...
    net_cash, net_cash_source = net_cash_equity(snapshot)
    if net_cash is None:
        net_cash = 0.0

    shares = _num(snapshot.get("sharesOutstanding"))
    mc = _num(snapshot.get("marketCap"))
    if not shares and price_usd and price_usd > 0:
        if mc:
            shares = mc / price_usd

    beta = _num(snapshot.get("beta"), DEFAULT_BETA)
    rf = risk_free_rate()
    exec_risk = dcf_engine.execution_risk_for(business_type)
    ke = dcf_engine.cost_of_equity(
        beta, risk_free=rf, equity_premium=DEFAULT_EQUITY_PREMIUM, execution_risk=exec_risk
    )

    total_debt = _num(snapshot.get("totalDebt")) or _num(snapshot.get("balance_total_debt")) or 0.0
    mc_val = mc if mc is not None else ((shares * price_usd) if shares and price_usd else 0.0)
    wacc_base = compute_true_wacc(ke, mc_val, total_debt, rf)

    wacc_bear = min(dcf_engine.KE_CAP, wacc_base + 0.015)
    wacc_bull = max(rf + 0.02, wacc_base - 0.010)

    rev_g_num = _num(rev_g) if rev_g is not None else None
    forward_growth_estimate = _num(snapshot.get("earningsGrowth"))

    median_ocf_usd: Optional[float] = None
    median_yoy_growth_pct: Optional[float] = None
    fcf_years_used = 0
    growth_anchor_source = "default_path"
    hist_anchor: Optional[float] = None

    if len(annual_rows) >= MIN_ANNUAL_OCF_YEARS:
        fcf, fcf_source = median_owner_earnings_fcf(annual_rows)
        median_yoy_growth_pct = median_ocf_yoy_growth_pct(annual_rows)
        fcf_years_used = len(annual_rows)
        ocf_vals = [_num(r.get("ocf")) for r in annual_rows]
        ocf_vals = [v for v in ocf_vals if v is not None]
        if ocf_vals:
            median_ocf_usd = float(statistics.median(ocf_vals))
        if median_yoy_growth_pct is not None:
            hist_anchor = median_yoy_growth_pct
            growth_anchor_source = "median_5y_ocf_yoy"
        elif rev_g_num is not None:
            growth_anchor_source = "revenue_growth"
        elif hist_cagr_pct is not None:
            hist_anchor = hist_cagr_pct
            growth_anchor_source = "hist_cagr_fallback"
    else:
        fcf, fcf_source = owner_earnings_fcf(snapshot)
        if rev_g_num is not None:
            growth_anchor_source = "revenue_growth"
        elif hist_cagr_pct is not None:
            hist_anchor = hist_cagr_pct
            growth_anchor_source = "hist_cagr_fallback"

    fcf_cagr_pct = hist_cagr_pct # Use the provided hist_cagr_pct as FCF CAGR proxy if available
    ocf_cagr_pct = median_yoy_growth_pct

    blended_anchor_pct = calculate_blended_growth_anchor(
        fcf_cagr=fcf_cagr_pct,
        revenue_growth=rev_g_num,
        ocf_cagr=ocf_cagr_pct,
        forward_growth_estimate=forward_growth_estimate,
    )
    growth_anchor_source = "blended_growth_anchor"
    base_path = build_base_growth_path(blended_anchor_pct, BASE_TERMINAL_G, business_type=business_type)

    out: Dict[str, Any] = {
        "fcf_usd": fcf,
        "fcf_source": fcf_source,
        "fcf_years_used": fcf_years_used,
        "median_ocf_usd": round(median_ocf_usd, 2) if median_ocf_usd is not None else None,
        "median_yoy_growth_pct": median_yoy_growth_pct,
        "growth_anchor_source": growth_anchor_source,
        "net_cash_usd": net_cash,
        "net_cash_source": net_cash_source,
        "shares": shares,
        "beta": beta,
        "risk_free_rate": round(rf, 4),
        "wacc_base": round(wacc_base, 4),
        "scenarios": {},
        "base_fair_value_usd": None,
        "available": False,
        "business_type": business_type,
    }

    if fcf is None or fcf <= 0 or not shares or shares <= 0:
        out["missing_reason"] = "Insufficient owner-earnings FCF or shares outstanding."
        return out

    # Dynamic bear/bull growth paths
    bear_path = [max(0.01, g * 0.5) for g in base_path]
    bull_path = [min(0.40, g * 1.3) for g in base_path]

    scenarios = {
        "bear": dcf_fair_value_per_share(
            fcf, shares, net_cash, bear_path, wacc_bear, BEAR_TERMINAL_G
        ),
        "base": dcf_fair_value_per_share(
            fcf, shares, net_cash, base_path, wacc_base, BASE_TERMINAL_G
        ),
        "bull": dcf_fair_value_per_share(
            fcf, shares, net_cash, bull_path, wacc_bull, BULL_TERMINAL_G
        ),
    }
    out["scenarios"] = {k: round(v, 2) if v is not None else None for k, v in scenarios.items()}
    base_fv = scenarios.get("base")
    out["base_fair_value_usd"] = round(base_fv, 2) if base_fv is not None else None
    out["available"] = base_fv is not None
    out["growth_path_base"] = [round(g, 4) for g in base_path]

    # Calculate Terminal Value % and Guardrails
    terminal_spread = wacc_base - BASE_TERMINAL_G
    out["wacc_terminal_spread_pct"] = round(terminal_spread * 100, 2)
    warning_flags = []

    if terminal_spread < 0.025:
        warning_flags.append("terminal_spread_low")

    sbc = _num(snapshot.get("stockBasedCompensation")) or 0.0
    sbc_to_fcf = (sbc / fcf) if fcf > 0 else 0.0
    out["sbc_to_fcf_pct"] = round(sbc_to_fcf * 100, 2)
    if sbc_to_fcf > 0.10:
        warning_flags.append("high_sbc")

    sector = snapshot.get("sector")
    sector_suitability = "high"
    if sector in ["Financial Services", "Financials", "Banks", "Insurance", "Real Estate", "Biotech"]:
        sector_suitability = "low"
        warning_flags.append("sector_unsuitable")

    # Estimate terminal value % of total value
    terminal_value_pct = None
    try:
        # Re-run Gordon growth terminal value to find its PV
        years = DCF_YEARS
        path = list(base_path[:years])
        while len(path) < years:
            path.append(path[-1] if path else 0.03)

        fcfs = [fcf]
        for g in path:
            fcfs.append(fcfs[-1] * (1.0 + g))

        terminal_fcf = fcfs[years] * (1.0 + BASE_TERMINAL_G)
        tv = terminal_fcf / terminal_spread
        tv_pv = tv / ((1.0 + wacc_base) ** years)

        equity = dcf_equity_value(fcf, base_path, wacc_base, BASE_TERMINAL_G)
        total_ev = equity + net_cash

        if total_ev > 0:
            terminal_value_pct = (tv_pv / total_ev) * 100
            out["terminal_value_pct"] = round(terminal_value_pct, 1)
            if terminal_value_pct > 85.0:
                warning_flags.append("terminal_value_high")
    except Exception:
        pass

    out["valuation_warning_flags"] = warning_flags
    out["sector_dcf_suitability"] = sector_suitability

    # Base confidence score on flags
    confidence = 80
    if warning_flags:
        confidence -= 15 * len(warning_flags)
    out["dcf_confidence_score"] = max(0, min(100, confidence))
    out["model_name"] = "Mature Owner-Earnings DCF"

    fcf_per_share = (fcf / shares) if (fcf and shares and shares > 0) else None
    _enrich_valuation_output(
        out,
        snapshot=snapshot,
        classification=classification,
        price_usd=price_usd,
        fcf_per_share=fcf_per_share,
        wacc=wacc_base,
        terminal_growth=BASE_TERMINAL_G,
        years=DCF_YEARS,
    )

    return out


def fetch_yfinance_valuation_snapshot(ticker: str) -> Dict[str, Any]:
    """
    yfinance .info plus cashflow / balance-sheet fallbacks for DCF inputs.
    """
    t_up = ticker.upper().strip()
    out: Dict[str, Any] = {"ticker": t_up}
    try:
        import yfinance as yf

        t = yf.Ticker(t_up)
        info = t.info or {}
        out.update(
            {
                "freeCashflow": info.get("freeCashflow"),
                "operatingCashflow": info.get("operatingCashflow"),
                "capitalExpenditures": info.get("capitalExpenditures"),
                "totalCash": info.get("totalCash"),
                "shortTermInvestments": info.get("shortTermInvestments"),
                "longTermInvestments": info.get("longTermInvestments"),
                "totalDebt": info.get("totalDebt"),
                "sharesOutstanding": info.get("sharesOutstanding"),
                "marketCap": info.get("marketCap"),
                "revenueGrowth": info.get("revenueGrowth"),
                "earningsGrowth": info.get("earningsGrowth"),
                "operatingMargins": info.get("operatingMargins"),
                "beta": info.get("beta"),
                "trailingEps": info.get("trailingEps"),
                "currentRatio": info.get("currentRatio"),
                "ebitda": info.get("ebitda"),
                "bookValue": info.get("bookValue"),
                "returnOnEquity": info.get("returnOnEquity"),
                "grossMargins": info.get("grossMargins"),
                "regularMarketPrice": info.get("regularMarketPrice"),
                "currentPrice": info.get("currentPrice"),
                "previousClose": info.get("previousClose")
                or info.get("regularMarketPreviousClose"),
                "longName": info.get("longName") or info.get("shortName") or t_up,
                "sector": info.get("sector"),
                "totalRevenue": info.get("totalRevenue"),
            }
        )

        try:
            cf = t.cashflow
            out["annual_cashflow_5y"] = _annual_cashflow_rows(cf)
            out["statement_free_cash_flow"] = _first_row_value(
                cf,
                ("Free Cash Flow",),
            )
            if out.get("operatingCashflow") is None:
                out["operatingCashflow"] = _first_row_value(
                    cf,
                    ("Operating Cash Flow",),
                )
            if out.get("capitalExpenditures") is None:
                out["capitalExpenditures"] = _first_row_value(
                    cf,
                    ("Capital Expenditure", "Purchase Of PPE"),
                )

            out["stockBasedCompensation"] = _first_row_value(
                cf,
                ("Stock Based Compensation", "Share Based Compensation"),
            )
            out["depreciation"] = _first_row_value(
                cf,
                (
                    "Depreciation And Amortization",
                    "Depreciation Amortization Depletion",
                    "Depreciation",
                ),
            )
            # Up to 5 fiscal years of capex magnitude for capex-growth / maintenance-capex.
            capex_hist = []
            for r in out.get("annual_cashflow_5y") or []:
                c = _num(r.get("capex"))
                if c is not None:
                    capex_hist.append(abs(c))
            out["capex_history_5y"] = capex_hist
        except Exception as exc:
            logger.debug("[valuation_inputs] cashflow statement failed %s: %s", t_up, exc)

        try:
            financials = t.financials
            out["operatingIncome"] = _first_row_value(
                financials,
                ("Operating Income",),
            )
            if out.get("totalRevenue") is None:
                out["totalRevenue"] = _first_row_value(
                    financials,
                    ("Total Revenue",),
                )
        except Exception as exc:
            logger.debug("[valuation_inputs] financials statement failed %s: %s", t_up, exc)

        try:
            bs = t.balance_sheet
            out["balance_cash_and_st_investments"] = _first_row_value(
                bs,
                (
                    "Cash Cash Equivalents And Short Term Investments",
                    "Cash And Cash Equivalents",
                ),
            )
            out["balance_investments_and_advances"] = _first_row_value(
                bs,
                ("Investments And Advances", "Long Term Investments"),
            )
            out["balance_total_debt"] = _first_row_value(
                bs,
                ("Total Debt",),
            )
        except Exception as exc:
            logger.debug("[valuation_inputs] balance sheet failed %s: %s", t_up, exc)

    except Exception as exc:
        logger.warning("[valuation_inputs] snapshot failed %s: %s", t_up, exc)

    return out

def compute_high_growth_dcf_scenarios(
    snapshot: Dict[str, Any],
    *,
    price_usd: Optional[float] = None,
    business_type: str = "other",
) -> Dict[str, Any]:
    """
    Bear / base / bull high-growth revenue-to-FCF scenarios.
    """
    net_cash, net_cash_source = net_cash_equity(snapshot)
    if net_cash is None:
        net_cash = 0.0

    shares = _num(snapshot.get("sharesOutstanding"))
    mc = _num(snapshot.get("marketCap"))
    if not shares and price_usd and price_usd > 0:
        if mc:
            shares = mc / price_usd

    beta = _num(snapshot.get("beta"), DEFAULT_BETA)
    rf = risk_free_rate()
    ke = capm_wacc(beta, risk_free=rf, equity_premium=DEFAULT_EQUITY_PREMIUM)

    total_debt = _num(snapshot.get("totalDebt")) or _num(snapshot.get("balance_total_debt")) or 0.0
    mc_val = mc if mc is not None else ((shares * price_usd) if shares and price_usd else 0.0)
    wacc_base = compute_true_wacc(ke, mc_val, total_debt, rf)

    wacc_bear = min(0.14, wacc_base + 0.015)
    wacc_bull = max(rf + 0.02, wacc_base - 0.010)

    rev_0 = _num(snapshot.get("totalRevenue"))
    rev_g_num = _num(snapshot.get("revenueGrowth"))

    if rev_0 is None or rev_0 <= 0 or not shares or shares <= 0:
        return {
            "available": False,
            "missing_reason": "Insufficient revenue or shares outstanding for High-Growth DCF."
        }

    if rev_g_num is None:
        return {
            "available": False,
            "missing_reason": "Missing revenue growth metric for High-Growth DCF."
        }

    gross_margins = _num(snapshot.get("grossMargins"))

    # Calculate current FCF margin
    fcf, fcf_source = owner_earnings_fcf(snapshot)
    fcf = fcf or _num(snapshot.get("freeCashflow")) or _num(snapshot.get("operatingCashflow"))
    current_fcf_margin = 0.0
    if fcf is not None and rev_0 > 0:
        current_fcf_margin = fcf / rev_0

    # Heuristics for mature FCF margins based on gross margins
    if gross_margins is not None:
        base_target_margin = max(0.15, gross_margins * 0.3) # E.g. 70% GM -> 21% FCF margin
    else:
        base_target_margin = 0.20

    target_fcf_margin_bear = max(0.08, base_target_margin * 0.5)
    target_fcf_margin_base = base_target_margin
    target_fcf_margin_bull = min(0.35, base_target_margin * 1.5)

    def calculate_hg_dcf(
        rev_g_initial: float,
        wacc: float,
        term_g: float,
        target_margin: float,
        years: int = 10
    ) -> float:
        revenue = rev_0
        fcfs = []
        for i in range(1, years + 1):
            g = rev_g_initial - (rev_g_initial - term_g) * (i / years)
            m = current_fcf_margin + (target_margin - current_fcf_margin) * (i / years)
            revenue *= (1.0 + g)
            fcf_val = revenue * m
            fcfs.append(fcf_val)

        pv = sum(fcfs[i] / ((1.0 + wacc) ** (i + 1)) for i in range(years))
        terminal_fcf = fcfs[-1] * (1.0 + term_g)
        if wacc <= term_g:
            return 0.0 # Invalid
        tv = terminal_fcf / (wacc - term_g)
        tv_pv = tv / ((1.0 + wacc) ** years)

        equity_value = pv + tv_pv + net_cash
        if equity_value < 0:
            return 0.0
        return float(equity_value / shares)

    rev_g_bear = max(0.0, rev_g_num * 0.5)
    rev_g_base = rev_g_num
    rev_g_bull = min(1.0, rev_g_num * 1.5)

    bear_fv = calculate_hg_dcf(rev_g_bear, wacc_bear, BEAR_TERMINAL_G, target_fcf_margin_bear)
    base_fv = calculate_hg_dcf(rev_g_base, wacc_base, BASE_TERMINAL_G, target_fcf_margin_base)
    bull_fv = calculate_hg_dcf(rev_g_bull, wacc_bull, BULL_TERMINAL_G, target_fcf_margin_bull)

    out = {
        "fcf_usd": fcf,
        "fcf_source": fcf_source,
        "fcf_years_used": 0,
        "growth_anchor_source": "revenue_growth",
        "net_cash_usd": net_cash,
        "net_cash_source": net_cash_source,
        "shares": shares,
        "beta": beta,
        "risk_free_rate": round(rf, 4),
        "wacc_base": round(wacc_base, 4),
        "scenarios": {
            "bear": round(bear_fv, 2) if bear_fv else None,
            "base": round(base_fv, 2) if base_fv else None,
            "bull": round(bull_fv, 2) if bull_fv else None,
        },
        "base_fair_value_usd": round(base_fv, 2) if base_fv else None,
        "available": bool(base_fv and base_fv > 0),
        "current_fcf_margin": round(current_fcf_margin, 4),
        "target_fcf_margin_base": round(target_fcf_margin_base, 4),
        "revenue_growth": round(rev_g_num, 4),
        "business_type": business_type,
    }

    warning_flags = ["high_growth_sensitivity"]
    terminal_spread = wacc_base - BASE_TERMINAL_G
    if terminal_spread < 0.025:
        warning_flags.append("terminal_spread_low")

    sector = snapshot.get("sector")
    if sector in ["Financial Services", "Financials", "Banks", "Insurance", "Real Estate", "Biotech"]:
        warning_flags.append("sector_unsuitable")

    out["valuation_warning_flags"] = warning_flags
    out["sector_dcf_suitability"] = "high"

    confidence = 70
    if len(warning_flags) > 1:
        confidence -= 15 * (len(warning_flags) - 1)
    out["dcf_confidence_score"] = max(0, min(100, confidence))
    out["model_name"] = "High-Growth Revenue-to-FCF DCF"

    classification = classify_business({
        "market_cap": _num(snapshot.get("marketCap")) or 0.0,
        "revenue_growth_yoy": rev_g_num or 0.0,
        "gross_margin": gross_margins or 0.0,
        "operating_margin": _num(snapshot.get("operatingMargins")) or 0.0,
        "fcf_margin": current_fcf_margin or 0.0,
        "roic": _num(snapshot.get("returnOnEquity")) or 0.0,
        "sector": snapshot.get("sector"),
    }) if business_type else {"business_type": business_type}
    classification.setdefault("business_type", business_type)
    fcf_per_share = (fcf / shares) if (fcf and shares and shares > 0) else None
    _enrich_valuation_output(
        out,
        snapshot=snapshot,
        classification=classification,
        price_usd=price_usd,
        fcf_per_share=fcf_per_share,
        wacc=wacc_base,
        terminal_growth=BASE_TERMINAL_G,
        years=10,
    )

    return out


def compute_supercycle_dcf_scenarios(
    snapshot: Dict[str, Any],
    *,
    seed: Dict[str, Any],
    classification: Dict[str, Any],
    price_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """AI capex-supercycle FCFF valuation (segment revenue + reinvestment via
    sales-to-capital). Returns the standard scenario dict shape plus V2 fields.
    """
    business_type = classification.get("business_type", "platform_reinvestment_supercycle")
    net_cash, net_cash_source = net_cash_equity(snapshot)
    if net_cash is None:
        net_cash = 0.0

    shares = _num(snapshot.get("sharesOutstanding"))
    mc = _num(snapshot.get("marketCap"))
    if not shares and price_usd and price_usd > 0 and mc:
        shares = mc / price_usd

    rev_0 = _num(snapshot.get("totalRevenue"))
    if rev_0 is None or rev_0 <= 0 or not shares or shares <= 0:
        return {"available": False, "missing_reason": "Insufficient revenue/shares for supercycle DCF."}

    beta = _num(snapshot.get("beta"), DEFAULT_BETA)
    rf = risk_free_rate()
    exec_risk = dcf_engine.execution_risk_for(business_type)
    ke = dcf_engine.cost_of_equity(
        beta, risk_free=rf, equity_premium=DEFAULT_EQUITY_PREMIUM, execution_risk=exec_risk
    )
    total_debt = _num(snapshot.get("totalDebt")) or _num(snapshot.get("balance_total_debt")) or 0.0
    wacc_base = compute_true_wacc(ke, mc or (shares * (price_usd or 0.0)), total_debt, rf)
    wacc_bear = min(dcf_engine.KE_CAP, wacc_base + 0.015)
    wacc_bull = max(rf + 0.02, wacc_base - 0.010)

    op_margin = _num(snapshot.get("operatingMargins"))
    if op_margin is None:
        oi = _num(snapshot.get("operatingIncome"))
        op_margin = (oi / rev_0) if oi else 0.30
    roic = _num(snapshot.get("returnOnEquity")) or 0.20

    tg_base = dcf_engine.dynamic_terminal_growth(business_type, rf)
    tg_bear = max(0.0, tg_base - 0.005)
    tg_bull = min(rf - 0.005, tg_base + 0.003)

    def _scenario(growth_mult: float, wacc: float, tg: float, margin_delta: float) -> Optional[Dict[str, Any]]:
        s = dict(seed)
        s["ai_growth"] = (_num(seed.get("ai_growth"), 0.30) or 0.30) * growth_mult
        s["core_growth"] = (_num(seed.get("core_growth"), 0.07) or 0.07) * growth_mult
        return dcf_engine.supercycle_value_per_share(
            revenue0=rev_0,
            seed=s,
            operating_margin=op_margin,
            tax_rate=DEFAULT_TAX_RATE,
            roic=roic,
            discount_rate=wacc,
            terminal_growth=tg,
            net_cash=net_cash,
            shares=shares,
            margin_trend=0.0,  # data-driven: no fabricated compression
            margin_target=max(op_margin, op_margin + margin_delta),
        )

    base = _scenario(1.0, wacc_base, tg_base, 0.05)
    bear = _scenario(0.6, wacc_bear, tg_bear, 0.0)
    bull = _scenario(1.3, wacc_bull, tg_bull, 0.10)

    base_fv = base.get("fair_value_per_share") if base else None
    out: Dict[str, Any] = {
        "fcf_usd": None,
        "fcf_source": "fcff_segment_model",
        "fcf_years_used": base.get("years") if base else None,
        "growth_anchor_source": "ai_supercycle_seed",
        "net_cash_usd": net_cash,
        "net_cash_source": net_cash_source,
        "shares": shares,
        "beta": beta,
        "risk_free_rate": round(rf, 4),
        "wacc_base": round(wacc_base, 4),
        "scenarios": {
            "bear": round(bear["fair_value_per_share"], 2) if bear else None,
            "base": round(base_fv, 2) if base_fv else None,
            "bull": round(bull["fair_value_per_share"], 2) if bull else None,
        },
        "base_fair_value_usd": round(base_fv, 2) if base_fv else None,
        "available": bool(base_fv and base_fv > 0),
        "business_type": business_type,
        "operating_margin": round(op_margin, 4),
        "roic": round(roic, 4),
        "terminal_growth_base": round(tg_base, 4),
        "supercycle_detail": base,
        "model_name": "AI Supercycle FCFF DCF",
        "dcf_confidence_score": 60,
        "sector_dcf_suitability": "high",
        "valuation_warning_flags": ["ai_monetization_uncertainty", "long_horizon_sensitivity"],
    }

    if not out["available"]:
        out["missing_reason"] = "Supercycle FCFF produced no positive base value."
        return out

    # Reverse DCF implied margin / ROIC (one unknown at a time) on a flat-margin
    # FCFF representation using the base growth path.
    base_path = [g for g in (base.get("growth_path") or [])] or dcf_engine.multi_stage_path(
        _num(seed.get("ai_growth"), 0.30) or 0.30, tg_base, base.get("years", 13)
    )
    years = len(base_path)
    implied_margin = implied_roic = None
    if price_usd and price_usd > 0:
        def _val_for_margin(m: float) -> float:
            v = dcf_engine.fcff_equity_value_per_share(
                revenue0=rev_0, growth_path=base_path,
                operating_margin_path=[m] * years, tax_rate=DEFAULT_TAX_RATE,
                roic=roic, discount_rate=wacc_base, terminal_growth=tg_base,
                net_cash=net_cash, shares=shares,
            )
            return v if v is not None else 0.0

        def _val_for_roic(r: float) -> float:
            v = dcf_engine.fcff_equity_value_per_share(
                revenue0=rev_0, growth_path=base_path,
                operating_margin_path=[op_margin] * years, tax_rate=DEFAULT_TAX_RATE,
                roic=r, discount_rate=wacc_base, terminal_growth=tg_base,
                net_cash=net_cash, shares=shares,
            )
            return v if v is not None else 0.0

        implied_margin = dcf_engine._bisect(_val_for_margin, price_usd, 0.02, 0.70)
        implied_roic = dcf_engine._bisect(_val_for_roic, price_usd, dcf_engine.ROIC_FLOOR, 0.80)

    fcff_year1_ps = None
    detail_path = base.get("growth_path") or []
    if detail_path:
        # Approximate per-share FCFF0 for implied-growth reverse DCF.
        fcff_year1_ps = (rev_0 * (1.0 + detail_path[0]) * op_margin * (1.0 - DEFAULT_TAX_RATE)) / shares

    _enrich_valuation_output(
        out,
        snapshot=snapshot,
        classification=classification,
        price_usd=price_usd,
        fcf_per_share=fcff_year1_ps,
        wacc=wacc_base,
        terminal_growth=tg_base,
        years=years,
    )
    out["implied_margin"] = round(implied_margin, 4) if implied_margin is not None else None
    out["implied_roic"] = round(implied_roic, 4) if implied_roic is not None else None
    return out
