"""Top-N portfolio backtest with transaction costs.

Each period (a rebalance) we long the top-N ranked names by model score, equal
weight, and earn their forward sector-relative (excess) return minus turnover
cost. This validates that the ranking has economic value, not just statistical
AUC (docs Rule 09: a model must beat the rule baseline under backtest with costs).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np


def run_backtest(dates: Sequence[int], scores: Sequence[float], forward_excess: Sequence[float],
                 tickers: Sequence[str], top_n: int = 10, cost_bps: float = 10.0,
                 periods_per_year: int = 4) -> Dict:
    """Simulate a long-only top-N excess-return strategy.

    cost_bps applies to one-way turnover each rebalance.
    Returns metrics + the equity curve.
    """
    dates = np.asarray(dates)
    scores = np.asarray(scores, dtype=float)
    forward_excess = np.asarray(forward_excess, dtype=float)
    tickers = np.asarray(tickers)

    period_ids = sorted(set(dates.tolist()))
    net_returns: List[float] = []
    turnovers: List[float] = []
    prev_holdings: set = set()

    for pid in period_ids:
        mask = dates == pid
        sc = scores[mask]
        ex = forward_excess[mask]
        tk = tickers[mask]
        if sc.size == 0:
            continue
        k = min(top_n, sc.size)
        top = np.argsort(-sc)[:k]
        holdings = set(tk[top].tolist())
        gross = float(np.mean(ex[top]))

        # one-way turnover vs previous period
        if prev_holdings:
            changed = len(holdings.symmetric_difference(prev_holdings)) / 2.0
            turnover = changed / max(len(holdings), 1)
        else:
            turnover = 1.0  # initial buy-in
        cost = turnover * (cost_bps / 1e4)
        net = gross - cost
        net_returns.append(net)
        turnovers.append(turnover)
        prev_holdings = holdings

    net = np.asarray(net_returns, dtype=float)
    if net.size == 0:
        return {"error": "no periods", "n_periods": 0}

    equity = np.cumprod(1.0 + net)
    running_max = np.maximum.accumulate(equity)
    max_dd = float((equity / running_max - 1.0).min())
    std = float(np.std(net, ddof=1)) if net.size > 1 else 0.0
    sharpe = float(np.mean(net) / std * np.sqrt(periods_per_year)) if std > 0 else 0.0

    return {
        "n_periods": int(net.size),
        "mean_excess_per_period": round(float(np.mean(net)), 6),
        "annualized_excess": round(float(np.mean(net) * periods_per_year), 6),
        "annualized_vol": round(std * np.sqrt(periods_per_year), 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 6),
        "hit_rate": round(float(np.mean(net > 0)), 4),
        "avg_turnover": round(float(np.mean(turnovers)), 4),
        "final_equity": round(float(equity[-1]), 6),
        "cost_bps": cost_bps,
        "top_n": top_n,
        "equity_curve": [round(float(e), 6) for e in equity],
    }
