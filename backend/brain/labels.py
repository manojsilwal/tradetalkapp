"""Supervised label creation: sector-relative forward outperformance.

Primary target: did the stock outperform its sector benchmark over the next
``horizon_days`` trading days?

    future_stock_return     = price[t+H] / price[t] - 1
    future_benchmark_return = bench[t+H] / bench[t] - 1
    future_excess_return    = future_stock_return - future_benchmark_return
    outperformed_benchmark  = future_excess_return > 0

Labels are excluded (None) when the full forward window is not available — we
never label on an incomplete future window.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from . import DEFAULT_HORIZON_DAYS


def forward_label(prices: Sequence[float], benchmark_prices: Sequence[float],
                  t: int, horizon_days: int = DEFAULT_HORIZON_DAYS) -> Optional[Dict[str, float]]:
    """Compute the label dict at index ``t`` or None if the window is incomplete."""
    n = len(prices)
    if benchmark_prices is None or len(benchmark_prices) != n:
        return None
    if t < 0 or t + horizon_days >= n:
        return None
    p0, p1 = prices[t], prices[t + horizon_days]
    b0, b1 = benchmark_prices[t], benchmark_prices[t + horizon_days]
    if p0 in (0, None) or b0 in (0, None):
        return None
    stock_ret = p1 / p0 - 1.0
    bench_ret = b1 / b0 - 1.0
    excess = stock_ret - bench_ret
    return {
        "horizon_days": int(horizon_days),
        "future_stock_return": float(stock_ret),
        "future_benchmark_return": float(bench_ret),
        "future_excess_return": float(excess),
        "outperformed_benchmark": bool(excess > 0),
    }


def build_labels_panel(price_panel: "Sequence[dict]",
                       horizon_days: int = DEFAULT_HORIZON_DAYS) -> List[Optional[Dict[str, float]]]:
    """Labels aligned 1:1 with build_features_panel input rows."""
    labels: List[Optional[Dict[str, float]]] = []
    for obs in price_panel:
        labels.append(
            forward_label(obs["prices"], obs["benchmark"], obs["as_of_idx"], horizon_days)
        )
    return labels
