"""Pure, dependency-light finance math.

Every function here is deterministic and side-effect free so it can be unit
tested in isolation (see backend/tests/test_brain_finance_math.py). No I/O, no
network, no global state. NumPy only.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

TRADING_DAYS = 252


def _arr(x: Sequence[float]) -> np.ndarray:
    return np.asarray(x, dtype=float)


def daily_returns(prices: Sequence[float]) -> np.ndarray:
    """Simple daily returns from a price series. Length = len(prices) - 1."""
    p = _arr(prices)
    if p.size < 2:
        return np.array([], dtype=float)
    return p[1:] / p[:-1] - 1.0


def cumulative_return(prices: Sequence[float], lookback: int) -> Optional[float]:
    """Return over ``lookback`` periods: prices[-1] / prices[-1-lookback] - 1.

    Returns ``None`` if there is not enough history (no fabrication).
    """
    p = _arr(prices)
    if lookback <= 0 or p.size <= lookback:
        return None
    denom = p[-1 - lookback]
    if denom == 0:
        return None
    return float(p[-1] / denom - 1.0)


def cagr(begin_value: float, end_value: float, years: float) -> Optional[float]:
    if begin_value is None or end_value is None or begin_value <= 0 or years <= 0:
        return None
    if end_value <= 0:
        return None
    return float((end_value / begin_value) ** (1.0 / years) - 1.0)


def annualized_volatility(returns: Sequence[float], periods: int = TRADING_DAYS) -> Optional[float]:
    r = _arr(returns)
    if r.size < 2:
        return None
    return float(np.std(r, ddof=1) * np.sqrt(periods))


def max_drawdown(prices: Sequence[float]) -> Optional[float]:
    """Maximum peak-to-trough decline (a non-positive number, e.g. -0.32)."""
    p = _arr(prices)
    if p.size < 2:
        return None
    running_max = np.maximum.accumulate(p)
    drawdowns = p / running_max - 1.0
    return float(drawdowns.min())


def downside_deviation(returns: Sequence[float], mar: float = 0.0,
                       periods: int = TRADING_DAYS) -> Optional[float]:
    r = _arr(returns)
    if r.size < 2:
        return None
    downside = np.clip(r - mar, a_min=None, a_max=0.0)
    return float(np.sqrt(np.mean(downside ** 2)) * np.sqrt(periods))


def sharpe_ratio(returns: Sequence[float], risk_free: float = 0.0,
                 periods: int = TRADING_DAYS) -> Optional[float]:
    r = _arr(returns)
    if r.size < 2:
        return None
    excess = r - risk_free / periods
    sd = np.std(excess, ddof=1)
    if sd == 0:
        return None
    return float(np.mean(excess) / sd * np.sqrt(periods))


def sortino_ratio(returns: Sequence[float], risk_free: float = 0.0,
                  periods: int = TRADING_DAYS) -> Optional[float]:
    r = _arr(returns)
    if r.size < 2:
        return None
    excess = r - risk_free / periods
    dd = downside_deviation(r, mar=risk_free / periods, periods=periods)
    if not dd:
        return None
    return float(np.mean(excess) * periods / dd)


def moving_average(prices: Sequence[float], window: int) -> Optional[float]:
    p = _arr(prices)
    if window <= 0 or p.size < window:
        return None
    return float(np.mean(p[-window:]))


# --- Fundamentals -----------------------------------------------------------

def _safe_div(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None:
        return None
    if den == 0:
        return None
    return float(num) / float(den)


def free_cash_flow(operating_cash_flow: Optional[float],
                   capital_expenditures: Optional[float]) -> Optional[float]:
    """OCF minus capex magnitude (capex may be reported as a negative number)."""
    if operating_cash_flow is None or capital_expenditures is None:
        return None
    return float(operating_cash_flow) - abs(float(capital_expenditures))


def fcf_yield(fcf_ttm: Optional[float], market_cap: Optional[float]) -> Optional[float]:
    return _safe_div(fcf_ttm, market_cap)


def gross_margin(gross_profit: Optional[float], revenue: Optional[float]) -> Optional[float]:
    return _safe_div(gross_profit, revenue)


def operating_margin(operating_income: Optional[float], revenue: Optional[float]) -> Optional[float]:
    return _safe_div(operating_income, revenue)


def net_margin(net_income: Optional[float], revenue: Optional[float]) -> Optional[float]:
    return _safe_div(net_income, revenue)


def debt_to_equity(total_debt: Optional[float], shareholders_equity: Optional[float]) -> Optional[float]:
    return _safe_div(total_debt, shareholders_equity)


def enterprise_value(market_cap: Optional[float], total_debt: Optional[float],
                     cash: Optional[float]) -> Optional[float]:
    if market_cap is None or total_debt is None or cash is None:
        return None
    return float(market_cap) + float(total_debt) - float(cash)


def roic(operating_income: Optional[float], tax_rate: float, total_debt: Optional[float],
         shareholders_equity: Optional[float], cash: Optional[float]) -> Optional[float]:
    """Approximate ROIC = NOPAT / invested_capital."""
    if operating_income is None or total_debt is None or shareholders_equity is None or cash is None:
        return None
    nopat = float(operating_income) * (1.0 - float(tax_rate))
    invested_capital = float(total_debt) + float(shareholders_equity) - float(cash)
    if invested_capital == 0:
        return None
    return nopat / invested_capital


def percentile_rank(value: float, population: Sequence[float]) -> Optional[float]:
    """Cross-sectional percentile of ``value`` within ``population`` in [0, 1].

    NaNs in the population are ignored. Returns None if no valid population.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    pop = _arr([v for v in population if v is not None and not np.isnan(v)])
    if pop.size == 0:
        return None
    return float(np.mean(pop <= value))


def clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))
