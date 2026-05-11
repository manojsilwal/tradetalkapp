"""Inverse-MASE-weighted ensemble (MASE vs seasonal naive)."""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from .baselines.seasonal_naive import seasonal_naive_forecast


def _mase_vs_seasonal_naive(history: np.ndarray, pred: float, horizon: int) -> float:
    if history.size < 3:
        return 1.0
    naive_one = seasonal_naive_forecast(history, horizon, season=5)
    denom = abs(float(history[-1]) - naive_one)
    if denom < 1e-12:
        denom = 1e-12
    return abs(pred - float(history[-1])) / denom


def weighted_inverse_mase(
    history: np.ndarray,
    horizon: int,
    member_preds: Dict[str, float],
    *,
    eps: float = 1e-6,
) -> Tuple[float, Dict[str, float]]:
    weights: Dict[str, float] = {}
    for name, pred in member_preds.items():
        m = _mase_vs_seasonal_naive(history, pred, horizon) + eps
        weights[name] = 1.0 / m
    s = sum(weights.values()) or 1.0
    for k in list(weights.keys()):
        weights[k] /= s
    blended = sum(weights[k] * member_preds[k] for k in weights)
    return float(blended), weights


def pinball_loss(y: float, q: float, tau: float) -> float:
    err = y - q
    return err * tau if err >= 0 else err * (tau - 1.0)
