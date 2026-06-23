"""Feature engineering for the finance brain.

CRITICAL: no lookahead bias. A feature computed "as of" index ``t`` may only use
data up to and including ``t``. The panel builder enforces this by slicing each
series to ``[: t + 1]`` before computing. See test_brain_features for the
guarantee test (mutating the future must not change a past feature row).

Functions return ``None`` for any feature that lacks enough history or inputs;
they never fabricate values.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from . import FEATURE_LIST
from . import finance_math as fm

# Trading-day lookbacks.
LB_1M, LB_3M, LB_6M, LB_12M = 21, 63, 126, 252

# Fundamental / intelligence features are passed through from the fundamentals
# snapshot (already point-in-time as of the filing date by construction).
_PASSTHROUGH = [
    "revenue_growth_yoy",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "roic",
    "debt_to_equity",
    "fcf_yield",
    "pe_ratio",
    "ev_ebitda",
    "valuation_percentile_5y",
    "capital_flow_score",
    "institutional_accumulation_score",
    "filing_risk_score",
    "tsfm_expected_return",
    "tsfm_band_width",
    "customer_concentration_score",
    "new_product_expansion_score",
    "management_tone_score",
    "sentiment_score",
]


def momentum_features(prices: Sequence[float],
                      benchmark_prices: Optional[Sequence[float]] = None) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "return_1m": fm.cumulative_return(prices, LB_1M),
        "return_3m": fm.cumulative_return(prices, LB_3M),
        "return_6m": fm.cumulative_return(prices, LB_6M),
        "return_12m": fm.cumulative_return(prices, LB_12M),
    }
    ma50 = fm.moving_average(prices, 50)
    ma200 = fm.moving_average(prices, 200)
    last = float(prices[-1]) if len(prices) else None
    out["price_vs_50dma"] = (last / ma50 - 1.0) if (last and ma50) else None
    out["price_vs_200dma"] = (last / ma200 - 1.0) if (last and ma200) else None

    out["relative_strength_3m"] = None
    out["relative_strength_6m"] = None
    if benchmark_prices is not None and len(benchmark_prices) >= 2:
        for key, lb in (("relative_strength_3m", LB_3M), ("relative_strength_6m", LB_6M)):
            s = fm.cumulative_return(prices, lb)
            b = fm.cumulative_return(benchmark_prices, lb)
            out[key] = (s - b) if (s is not None and b is not None) else None
    return out


def risk_features(prices: Sequence[float]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "volatility_3m": None,
        "volatility_6m": None,
        "max_drawdown_6m": None,
    }
    p = np.asarray(prices, dtype=float)
    if p.size > LB_3M:
        out["volatility_3m"] = fm.annualized_volatility(fm.daily_returns(p[-(LB_3M + 1):]))
    if p.size > LB_6M:
        out["volatility_6m"] = fm.annualized_volatility(fm.daily_returns(p[-(LB_6M + 1):]))
        out["max_drawdown_6m"] = fm.max_drawdown(p[-LB_6M:])
    return out


def build_feature_row(prices: Sequence[float],
                      benchmark_prices: Optional[Sequence[float]],
                      fundamentals: Optional[Dict[str, float]] = None) -> Dict[str, Optional[float]]:
    """Build one feature row (all FEATURE_LIST keys) as of the END of ``prices``.

    ``prices`` / ``benchmark_prices`` must already be sliced to the as-of point.
    ``fundamentals`` is a point-in-time snapshot of pre-computed fundamental
    ratios (see finance_math + the data layer).
    """
    row: Dict[str, Optional[float]] = {k: None for k in FEATURE_LIST}
    row.update(momentum_features(prices, benchmark_prices))
    row.update(risk_features(prices))
    fundamentals = fundamentals or {}
    for key in _PASSTHROUGH:
        if key in fundamentals and fundamentals[key] is not None:
            row[key] = float(fundamentals[key])
    return row


def point_in_time_slice(series: Sequence[float], as_of_idx: int) -> List[float]:
    """Return ``series[: as_of_idx + 1]`` — the only data visible at ``as_of_idx``."""
    return list(series[: as_of_idx + 1])


def build_features_panel(price_panel: "Sequence[dict]") -> List[Dict[str, Optional[float]]]:
    """Build feature rows for a panel of point-in-time observations.

    ``price_panel`` is a list of dicts, each:
        {"ticker", "date", "prices" (full series), "benchmark" (full series),
         "as_of_idx" (int), "fundamentals" (dict)}
    Only data up to ``as_of_idx`` is used — enforced here, not trusted to callers.
    """
    rows: List[Dict[str, Optional[float]]] = []
    for obs in price_panel:
        idx = obs["as_of_idx"]
        prices = point_in_time_slice(obs["prices"], idx)
        bench = point_in_time_slice(obs["benchmark"], idx) if obs.get("benchmark") is not None else None
        feats = build_feature_row(prices, bench, obs.get("fundamentals"))
        feats = {"ticker": obs["ticker"], "date": obs["date"], **feats}
        rows.append(feats)
    return rows
