"""HTTP client for TimesFM microservice + deterministic mock."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config_loader import load_yaml_cached
from .timesfm_constants import (
    DEFAULT_MODEL_LABEL,
    IDX_MEAN,
    IDX_Q10,
    IDX_Q50,
    IDX_Q90,
    NUM_QUANTILE_CHANNELS,
)

logger = logging.getLogger(__name__)


def _cycle_seed(ticker: str, cycle_id: str) -> int:
    h = hashlib.sha256(f"{ticker}:{cycle_id}".encode()).hexdigest()
    return int(h[:16], 16)


class MockTimesFMClient:
    """
    Shape-correct quantiles for offline tests and eval harnesses ONLY.

    Truthful-data contract: this client must never serve user-facing
    forecasts — `run_predictor_forecast` requires the real TimesFM service
    (or explicit baselines-only mode) and otherwise reports insufficient_data.
    """

    def __init__(self, model_version: str = DEFAULT_MODEL_LABEL) -> None:
        self.model_version = model_version

    def forecast_price_path(
        self,
        log_prices: np.ndarray,
        max_horizon: int,
        *,
        ticker: str,
        cycle_id: str,
    ) -> np.ndarray:
        """
        Return array (H, 10) — channels: mean, q10..q90 per model layout.
        """
        rng = np.random.default_rng(_cycle_seed(ticker, cycle_id))
        last = float(log_prices[-1]) if log_prices.size else 0.0
        out = np.zeros((max_horizon, NUM_QUANTILE_CHANNELS), dtype=np.float64)
        vol = 0.008 + rng.random() * 0.004
        drift = rng.normal(0, vol * 0.3)
        for h in range(max_horizon):
            step = last + drift * (h + 1) + rng.normal(0, vol * np.sqrt(h + 1))
            spread = vol * (3.0 + h * 0.02)
            q10 = step - spread * 1.28
            q50 = step
            q90 = step + spread * 1.28
            mean = (q10 + q50 + q90) / 3.0
            row = np.zeros(NUM_QUANTILE_CHANNELS, dtype=np.float64)
            row[IDX_MEAN] = mean
            row[IDX_Q10] = min(q10, q50, q90)
            row[IDX_Q50] = q50
            row[IDX_Q90] = max(q10, q50, q90)
            # linearly interpolate q20–q40, q60–q80 between q10–q50–q90
            for i in range(2, 5):
                t = (i - 1) / 4.0
                row[i] = row[IDX_Q10] * (1 - t) + row[IDX_Q50] * t
            for i in range(6, 9):
                t = (i - 5) / 4.0
                row[i] = row[IDX_Q50] * (1 - t) + row[IDX_Q90] * t
            out[h] = row
        return out


async def fetch_timesfm_forecast_http(
    *,
    inputs: List[float],
    horizon: int,
    config_hash: str,
    model_version: str,
) -> Optional[Dict[str, Any]]:
    """Call remote TimesFM service; returns None if URL unset or failure."""
    url = (os.environ.get("TIMESFM_SERVICE_URL") or "").strip().rstrip("/")
    token = (os.environ.get("TIMESFM_SERVICE_TOKEN") or "").strip()
    if not url:
        return None
    try:
        import httpx
    except ImportError:
        logger.warning("[TimesFM] httpx unavailable")
        return None

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = {
        "inputs": inputs,
        "horizon": horizon,
        "config_hash": config_hash,
        "model_version": model_version,
    }
    timeout = float(os.environ.get("TIMESFM_HTTP_TIMEOUT_S", "45") or "45")
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{url}/forecast", json=body, headers=headers)
        if r.status_code >= 400:
            logger.warning("[TimesFM] HTTP %s: %s", r.status_code, r.text[:200])
            return None
        return r.json()


def http_quantiles_to_numpy_path(payload: Dict[str, Any]) -> Optional[np.ndarray]:
    """
    Normalize microservice JSON into ``(H, 10)`` quantile channels.

    Accepts ``quantiles`` as list-of-rows; pads narrow rows to length 10.
    """
    q = payload.get("quantiles")
    if q is None:
        return None
    arr = np.asarray(q, dtype=np.float64)
    if arr.ndim != 2:
        return None
    n_rows, n_cols = arr.shape
    out = np.zeros((n_rows, NUM_QUANTILE_CHANNELS), dtype=np.float64)
    take = min(NUM_QUANTILE_CHANNELS, n_cols)
    out[:, :take] = arr[:, :take]
    return out


def max_horizon_from_config() -> int:
    cfg = load_yaml_cached("timesfm_forecast_config.yaml")
    return int(cfg.get("max_horizon") or 256)
