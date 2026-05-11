"""Naive baseline: last value."""

from __future__ import annotations

import numpy as np


def naive_forecast(series: np.ndarray, horizon: int) -> float:
    if series.size == 0 or horizon < 1:
        return float("nan")
    return float(series[-1])
