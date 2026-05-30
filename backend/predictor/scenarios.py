"""Map quantiles to 3Y scenario prices for the decision-terminal roadmap."""

from __future__ import annotations

import math
from typing import Optional, Tuple


# Plausible 3Y scenario band vs spot (prevents runaway geometric extrapolation).
_MIN_3Y_RATIO = 0.35
_MAX_3Y_RATIO = 2.75


def extrapolate_geometric_3y(
    spot: float,
    horizon_days: int,
    price_at_horizon: float,
) -> Optional[float]:
    if spot <= 0 or horizon_days <= 0 or price_at_horizon <= 0:
        return None
    daily_log = math.log(price_at_horizon / spot) / float(horizon_days)
    raw = spot * math.exp(daily_log * 252.0 * 3.0)
    return max(spot * _MIN_3Y_RATIO, min(spot * _MAX_3Y_RATIO, raw))


def bull_base_bear_3y_from_63d(
    spot: float,
    q10_63: float,
    q50_63: float,
    q90_63: float,
    *,
    horizon_days: int = 63,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    return (
        extrapolate_geometric_3y(spot, horizon_days, q90_63),
        extrapolate_geometric_3y(spot, horizon_days, q50_63),
        extrapolate_geometric_3y(spot, horizon_days, q10_63),
    )
