"""EWMA level forecast."""

from __future__ import annotations

import numpy as np


def ewma_forecast(series: np.ndarray, horizon: int, alpha: float = 0.25) -> float:
    if series.size == 0 or horizon < 1:
        return float("nan")
    level = float(series[0])
    for x in series[1:]:
        level = alpha * float(x) + (1.0 - alpha) * level
    return level
