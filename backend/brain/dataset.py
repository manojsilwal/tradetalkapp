"""Synthetic, deterministic datasets for offline training/testing.

Generates a panel with a *known* signal so models can demonstrably learn it
(AUC > 0.5) without any network or real market data. The signal loads on
economically sensible features (momentum/quality/valuation positive, risk/expensive
negative) so the rule baseline also correlates.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from . import FEATURE_LIST

# Features that carry true signal in the synthetic generator.
_POSITIVE = ["return_3m", "return_6m", "roic", "fcf_yield", "operating_margin",
             "relative_strength_3m", "capital_flow_score", "tsfm_expected_return"]
_NEGATIVE = ["volatility_3m", "pe_ratio", "ev_ebitda", "filing_risk_score",
             "max_drawdown_6m", "tsfm_band_width"]


def make_true_weights(signal: float = 1.4) -> np.ndarray:
    w = np.zeros(len(FEATURE_LIST))
    idx = {f: i for i, f in enumerate(FEATURE_LIST)}
    for f in _POSITIVE:
        w[idx[f]] = signal
    for f in _NEGATIVE:
        w[idx[f]] = -signal
    return w


def synthetic_panel(n_tickers: int = 60, n_periods: int = 16, seed: int = 0,
                    signal: float = 1.4, missing_rate: float = 0.03,
                    period_spacing_days: int = 63) -> Dict:
    """Return a panel dict consumable by pipeline/backtest/inference.

    Keys:
      rows     : list[dict]  feature rows (FEATURE_LIST keys; some None)
      X        : np.ndarray  (N, D) same data as matrix (NaN where missing)
      y        : np.ndarray  (N,)   binary outperformance label
      excess   : np.ndarray  (N,)   forward excess return (continuous)
      dates    : np.ndarray  (N,)   integer period index
      tickers  : list[str]   (N,)   ticker per row
    """
    rng = np.random.default_rng(seed)
    d = len(FEATURE_LIST)
    w = make_true_weights(signal)
    ticker_names = [f"T{j:03d}" for j in range(n_tickers)]

    X_blocks, y_all, excess_all, date_all, tick_all = [], [], [], [], []
    for p in range(n_periods):
        Z = rng.normal(0.0, 1.0, size=(n_tickers, d))
        logit = Z @ w / np.sqrt(np.count_nonzero(w))
        noise = rng.normal(0.0, 1.0, size=n_tickers)
        prob = 1.0 / (1.0 + np.exp(-(logit)))
        y = (rng.uniform(size=n_tickers) < prob).astype(int)
        excess = 0.02 * logit + 0.01 * noise  # forward sector-relative return
        X_blocks.append(Z)
        y_all.append(y)
        excess_all.append(excess)
        # Dates in trading-day units so they share the label horizon's unit and
        # the purged split works correctly (non-overlapping forward windows).
        date_all.append(np.full(n_tickers, p * period_spacing_days))
        tick_all.extend(ticker_names)

    X = np.vstack(X_blocks)
    # Inject missingness.
    if missing_rate > 0:
        mask = rng.uniform(size=X.shape) < missing_rate
        X = X.copy()
        X[mask] = np.nan

    rows: List[Dict] = []
    for i in range(X.shape[0]):
        row = {FEATURE_LIST[j]: (None if np.isnan(X[i, j]) else float(X[i, j]))
               for j in range(d)}
        rows.append(row)

    return {
        "rows": rows,
        "X": X,
        "y": np.concatenate(y_all),
        "excess": np.concatenate(excess_all),
        "dates": np.concatenate(date_all),
        "tickers": tick_all,
    }


def make_price_series(n: int = 300, seed: int = 1, drift: float = 0.0005,
                      vol: float = 0.01) -> np.ndarray:
    """A simple geometric random-walk price series for feature/label tests."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, size=n)
    return 100.0 * np.cumprod(1.0 + rets)
