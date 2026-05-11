"""Random-walk with drift estimated from trailing returns."""

from __future__ import annotations

import numpy as np


def drift_forecast(series: np.ndarray, horizon: int) -> float:
    if series.size < 2 or horizon < 1:
        return float("nan")
    rets = np.diff(series.astype(np.float64))
    mu = float(np.mean(rets))
    last = float(series[-1])
    return last + mu * float(horizon)
