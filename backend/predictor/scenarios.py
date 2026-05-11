"""Map quantiles to 3Y scenario prices for the decision-terminal roadmap."""

from __future__ import annotations

import math
from typing import Optional, Tuple


def extrapolate_geometric_3y(
    spot: float,
    horizon_days: int,
    price_at_horizon: float,
) -> Optional[float]:
    if spot <= 0 or horizon_days <= 0 or price_at_horizon <= 0:
        return None
    daily_log = math.log(price_at_horizon / spot) / float(horizon_days)
    return spot * math.exp(daily_log * 252.0 * 3.0)


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
