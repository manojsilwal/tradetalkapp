"""
Model-as-strategy walk-forward backtest (Phase 4).

Answers the question the Strategy Lab can't: *"if I had traded the numeric
forecaster's signals over the last decade, what would have happened?"*

At each rebalance date the ensemble (baselines + learned weights, the same
math the live predictor uses minus the LLM narrative) forecasts the
``horizon`` close for every ticker using ONLY prior data. The strategy goes
long, equal-weight, every ticker whose expected return clears ``threshold``;
otherwise that slice stays in cash. The benchmark is an equal-weight
buy-and-hold of the same universe, so alpha is attributable to the model and
not to universe selection.

Fully deterministic given the data lake — no network, no LLM — which makes
it both a product surface (``POST /harness/model-backtest``) and a regression
gate for forecaster changes.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HORIZON_TD = {"1d": 1, "5d": 5, "21d": 21, "63d": 63}


@dataclass
class ModelBacktestResult:
    params: Dict[str, Any]
    n_tickers: int = 0
    n_rebalances: int = 0
    strategy_curve: List[Dict[str, Any]] = field(default_factory=list)
    benchmark_curve: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "params": self.params,
            "n_tickers": self.n_tickers,
            "n_rebalances": self.n_rebalances,
            "strategy_curve": self.strategy_curve,
            "benchmark_curve": self.benchmark_curve,
            "stats": self.stats,
            "elapsed_s": self.elapsed_s,
        }


def _load_frame(ticker: str):
    try:
        import pandas as pd

        from backend.data_lake.config import PRICES_DIR

        path = os.path.join(PRICES_DIR, f"{ticker.upper()}.parquet")
        if not os.path.isfile(path):
            return None
        df = pd.read_parquet(path, columns=["Close"])
        if df.empty or len(df) < 260:
            return None
        df = df.copy()
        df.index = pd.to_datetime(df.index, errors="coerce", utc=True)
        df = df[df.index.notna()]
        return df["Close"].astype(float)
    except Exception:
        return None


def _ensemble_q50(history, td: int, learned: Dict[str, float]) -> Optional[float]:
    import numpy as np

    from backend.predictor.baselines import (
        drift_forecast, ewma_forecast, naive_forecast, seasonal_naive_forecast,
    )
    from backend.predictor.ensemble import weighted_inverse_mase
    from backend.predictor.learned_weights import blend_weights

    arr = np.asarray(history, dtype=np.float64)
    if arr.size < 64:
        return None
    members = {
        "naive": naive_forecast(arr, td),
        "seasonal_naive": seasonal_naive_forecast(arr, td),
        "ewma": ewma_forecast(arr, td),
        "drift": drift_forecast(arr, td),
    }
    blended, wts = weighted_inverse_mase(arr, td, members)
    if learned:
        wts = blend_weights(wts, learned)
        blended = sum(wts[k] * members[k] for k in wts)
    return float(blended)


def _max_drawdown(values: List[float]) -> float:
    peak = -float("inf")
    mdd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def run_model_backtest(
    *,
    tickers: Optional[List[str]] = None,
    horizon: str = "21d",
    threshold: float = 0.0,
    start: str = "2015-01-01",
    max_tickers: int = 20,
    initial_value: float = 10_000.0,
) -> ModelBacktestResult:
    """Walk-forward backtest of the ensemble forecaster as a trading strategy."""
    import numpy as np
    import pandas as pd

    from backend.predictor.learned_weights import load_weights

    started = time.time()
    td = HORIZON_TD.get(horizon, 21)
    params = {
        "horizon": horizon,
        "threshold": threshold,
        "start": start,
        "max_tickers": max_tickers,
        "initial_value": initial_value,
    }
    result = ModelBacktestResult(params=params)

    if not tickers:
        try:
            from backend.data_lake.config import PRICES_DIR

            tickers = sorted(
                f[:-8] for f in os.listdir(PRICES_DIR) if f.endswith(".parquet")
            )[: max(1, max_tickers)]
        except Exception:
            tickers = []
    tickers = [t.upper() for t in (tickers or [])][: max(1, max_tickers)]

    frames: Dict[str, Any] = {}
    for t in tickers:
        s = _load_frame(t)
        if s is not None:
            frames[t] = s
    result.n_tickers = len(frames)
    if not frames:
        result.stats = {"error": "no data-lake price history available"}
        result.elapsed_s = round(time.time() - started, 1)
        return result

    panel = pd.DataFrame(frames).sort_index().ffill().dropna(how="all")
    start_ts = pd.Timestamp(start, tz="UTC")
    panel = panel[panel.index >= start_ts]
    if len(panel) < td * 4:
        result.stats = {"error": "not enough history after start date"}
        result.elapsed_s = round(time.time() - started, 1)
        return result

    learned_all = load_weights()
    learned = learned_all.get(horizon) or {}

    # Rebalance every `td` trading days; warmup of 64 rows for the forecasters.
    rebalance_idx = list(range(64, len(panel) - td, td))
    strat_value = initial_value
    bench_value = initial_value
    strat_curve: List[Dict[str, Any]] = []
    bench_curve: List[Dict[str, Any]] = []
    period_strat_returns: List[float] = []
    period_bench_returns: List[float] = []
    n_signals = 0

    for i in rebalance_idx:
        date = panel.index[i]
        exit_i = i + td
        selected: List[str] = []
        for t in panel.columns:
            history = panel[t].iloc[: i + 1].dropna()
            spot = float(history.iloc[-1]) if len(history) else 0.0
            if spot <= 0 or len(history) < 64:
                continue
            q50 = _ensemble_q50(history.values, td, learned)
            if q50 is None:
                continue
            if (q50 / spot - 1.0) > threshold:
                selected.append(t)

        # Strategy period return: equal weight over selected names, else cash.
        if selected:
            rets = []
            for t in selected:
                p0 = float(panel[t].iloc[i])
                p1 = float(panel[t].iloc[exit_i])
                if p0 > 0 and not (math.isnan(p0) or math.isnan(p1)):
                    rets.append(p1 / p0 - 1.0)
            period_ret = sum(rets) / len(rets) if rets else 0.0
            n_signals += len(selected)
        else:
            period_ret = 0.0

        # Benchmark: equal-weight hold of the full universe over same window.
        b_rets = []
        for t in panel.columns:
            p0 = float(panel[t].iloc[i])
            p1 = float(panel[t].iloc[exit_i])
            if p0 > 0 and not (math.isnan(p0) or math.isnan(p1)):
                b_rets.append(p1 / p0 - 1.0)
        bench_ret = sum(b_rets) / len(b_rets) if b_rets else 0.0

        strat_value *= 1.0 + period_ret
        bench_value *= 1.0 + bench_ret
        period_strat_returns.append(period_ret)
        period_bench_returns.append(bench_ret)
        strat_curve.append(
            {"date": date.date().isoformat(), "value": round(strat_value, 2),
             "n_positions": len(selected)}
        )
        bench_curve.append(
            {"date": date.date().isoformat(), "value": round(bench_value, 2)}
        )

    result.n_rebalances = len(strat_curve)
    result.strategy_curve = strat_curve
    result.benchmark_curve = bench_curve

    if strat_curve:
        years = max(1e-9, (len(rebalance_idx) * td) / 252.0)
        strat_cagr = (strat_value / initial_value) ** (1.0 / years) - 1.0
        bench_cagr = (bench_value / initial_value) ** (1.0 / years) - 1.0
        rets_arr = np.asarray(period_strat_returns)
        periods_per_year = 252.0 / td
        sharpe = 0.0
        if rets_arr.size > 2 and float(np.std(rets_arr)) > 1e-12:
            sharpe = float(np.mean(rets_arr) / np.std(rets_arr) * math.sqrt(periods_per_year))
        result.stats = {
            "strategy_final_value": round(strat_value, 2),
            "benchmark_final_value": round(bench_value, 2),
            "strategy_cagr": round(strat_cagr, 4),
            "benchmark_cagr": round(bench_cagr, 4),
            "excess_cagr": round(strat_cagr - bench_cagr, 4),
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(_max_drawdown([c["value"] for c in strat_curve]), 4),
            "avg_positions_per_rebalance": round(n_signals / max(1, len(rebalance_idx)), 2),
        }

    result.elapsed_s = round(time.time() - started, 1)
    logger.info("[ModelBacktest] %s", result.stats)
    return result
