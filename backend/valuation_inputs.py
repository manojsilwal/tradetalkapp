"""
Unified valuation inputs for owner-earnings DCF (yfinance + statement fallbacks).

Used by decision_terminal valuation models — not investment advice.
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_OCF_LABELS = ("Operating Cash Flow",)
_CAPEX_LABELS = ("Capital Expenditure", "Purchase Of PPE")
MIN_ANNUAL_OCF_YEARS = 3
MAX_ANNUAL_OCF_YEARS = 5

DEFAULT_RISK_FREE = 0.0446
DEFAULT_EQUITY_PREMIUM = 0.04
DEFAULT_BETA = 1.0
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
    """Median owner earnings (OCF − |capex|) across annual rows."""
    earnings: List[float] = []
    for row in rows:
        ocf = _num(row.get("ocf"))
        if ocf is None:
            continue
        oe = _owner_earnings(ocf, _num(row.get("capex")))
        if oe > 0:
            earnings.append(oe)
    if not earnings:
        return None, "none"
    return float(statistics.median(earnings)), "median_5y_owner_earnings"


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
    wacc = rf + b * equity_premium
    return max(rf + 0.02, min(0.14, wacc))


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
    Uses balance-sheet fallbacks when .info fields are missing.
    """
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

    net = cash + st_inv + lt_inv - debt
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


def build_base_growth_path(
    revenue_growth: Optional[float],
    hist_cagr_pct: Optional[float],
) -> List[float]:
    """
    Declining 5Y FCF growth for the base case (6% → 3% by default).
    When revenue or historical growth is weak, scale the path down toward 2%.
    """
    path = list(BASE_GROWTH_PATH)
    anchor: Optional[float] = None
    if revenue_growth is not None:
        rg = float(revenue_growth)
        anchor = rg / 100.0 if abs(rg) > 1.0 else rg
    elif hist_cagr_pct is not None:
        anchor = float(hist_cagr_pct) / 100.0

    if anchor is None:
        return path

    anchor = max(0.02, min(0.10, anchor))
    if anchor >= 0.055:
        return path

    scale = anchor / path[0]
    scaled = [max(0.02, g * scale) for g in path]
    for i in range(1, len(scaled)):
        scaled[i] = min(scaled[i], scaled[i - 1])
    return scaled


def compute_dcf_scenarios(
    snapshot: Dict[str, Any],
    *,
    hist_cagr_pct: Optional[float] = None,
    price_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Bear / base / bull owner-earnings DCF fair values per share.
    Returns dict with fair_value base + scenarios + provenance inputs.
    """
    net_cash, net_cash_source = net_cash_equity(snapshot)
    if net_cash is None:
        net_cash = 0.0

    shares = _num(snapshot.get("sharesOutstanding"))
    if not shares and price_usd and price_usd > 0:
        mc = _num(snapshot.get("marketCap"))
        if mc:
            shares = mc / price_usd

    beta = _num(snapshot.get("beta"), DEFAULT_BETA)
    rf = risk_free_rate()
    wacc_base = capm_wacc(beta, risk_free=rf, equity_premium=DEFAULT_EQUITY_PREMIUM)
    wacc_bear = min(0.14, wacc_base + 0.009)
    wacc_bull = max(rf + 0.02, wacc_base - 0.006)

    rev_g = snapshot.get("revenueGrowth")
    rev_g_num = _num(rev_g) if rev_g is not None else None

    annual_rows = snapshot.get("annual_cashflow_5y") or []
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

    if growth_anchor_source == "median_5y_ocf_yoy":
        base_path = build_base_growth_path(None, hist_anchor)
    elif growth_anchor_source == "revenue_growth":
        base_path = build_base_growth_path(rev_g_num, hist_anchor if hist_anchor is not None else None)
    elif growth_anchor_source == "hist_cagr_fallback":
        base_path = build_base_growth_path(None, hist_anchor)
    else:
        base_path = build_base_growth_path(None, None)

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
    }

    if fcf is None or fcf <= 0 or not shares or shares <= 0:
        out["missing_reason"] = "Insufficient owner-earnings FCF or shares outstanding."
        return out

    scenarios = {
        "bear": dcf_fair_value_per_share(
            fcf, shares, net_cash, BEAR_GROWTH_PATH, wacc_bear, BEAR_TERMINAL_G
        ),
        "base": dcf_fair_value_per_share(
            fcf, shares, net_cash, base_path, wacc_base, BASE_TERMINAL_G
        ),
        "bull": dcf_fair_value_per_share(
            fcf, shares, net_cash, BULL_GROWTH_PATH, wacc_bull, BULL_TERMINAL_G
        ),
    }
    out["scenarios"] = {k: round(v, 2) if v is not None else None for k, v in scenarios.items()}
    base_fv = scenarios.get("base")
    out["base_fair_value_usd"] = round(base_fv, 2) if base_fv is not None else None
    out["available"] = base_fv is not None
    out["growth_path_base"] = [round(g, 4) for g in base_path]
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
        except Exception as exc:
            logger.debug("[valuation_inputs] cashflow statement failed %s: %s", t_up, exc)

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
