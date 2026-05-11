"""Feature assembly for predictor runs — prices + optional PIT fundamentals."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from .pit import FACTOR_TO_COLUMN, as_of


def default_as_of_date() -> date:
    return datetime.now(timezone.utc).date()


def snapshot_pit_factors(
    ticker: str,
    as_of_d: date,
    factors: Optional[List[str]] = None,
) -> Dict[str, Optional[float]]:
    """Pull a small set of PIT fundamentals for metadata / covariates."""
    want = factors or ["roe", "gross_margin", "net_margin"]
    out: Dict[str, Optional[float]] = {}
    for f in want:
        if f.lower() not in FACTOR_TO_COLUMN:
            continue
        out[f] = as_of(ticker, f, as_of_d)
    return out


def build_log_price_inputs(level_prices: np.ndarray) -> np.ndarray:
    """Log prices for TimesFM-style heads (positive levels)."""
    x = np.maximum(level_prices.astype(np.float64), 1e-8)
    return np.log(x)


def feature_bundle_for_ledger(
    *,
    ticker: str,
    cycle_id: str,
    spot: float,
    pit_snapshot: Dict[str, Optional[float]],
) -> Dict[str, Any]:
    """Structured blob suitable for ``output`` extras / manifests."""
    return {
        "ticker": ticker.upper(),
        "cycle_id": cycle_id,
        "spot_usd": spot,
        "pit_factors": pit_snapshot,
    }
