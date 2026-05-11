"""Seasonal naive: value from ``season`` days ago (weekly default)."""

from __future__ import annotations

import numpy as np


def seasonal_naive_forecast(series: np.ndarray, horizon: int, season: int = 5) -> float:
    if series.size == 0 or horizon < 1:
        return float("nan")
    idx = series.size - 1 - season
    if idx < 0:
        return float(series[-1])
    return float(series[idx])
