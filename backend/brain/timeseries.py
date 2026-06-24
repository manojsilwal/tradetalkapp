"""TimesFM time-series head -> brain features (offline, pure-Python).

This is the bridge that keeps the existing TimesFM predictor
(``backend/predictor/``) effectively in use inside the new brain. TimesFM
produces probabilistic price-path quantiles (q10/q50/q90 in USD per horizon).
We convert those bands + the current price into:

  - forward-looking metrics surfaced to the UI (expected return, downside,
    upside, uncertainty band width, skew, prob-up), and
  - two model features (``tsfm_expected_return``, ``tsfm_band_width``) the
    cross-sectional classifier consumes.

Because the bands are absolute USD anchors, a live price move changes the
implied forward return WITHOUT re-running TimesFM — exactly like the DCF anchor.
That makes the Reflex layer able to keep the time-series view fresh for free.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# The brain's label horizon; pick the matching TimesFM band.
DEFAULT_FORECAST_HORIZON = "63d"


def bands_for_horizon(bands: Sequence[Dict], horizon: str = DEFAULT_FORECAST_HORIZON
                      ) -> Optional[Dict]:
    """Find the {horizon, q10, q50, q90} entry for ``horizon`` (USD bands)."""
    for b in bands or []:
        if str(b.get("horizon")) == str(horizon):
            return b
    return None


def forward_metrics(q10: float, q50: float, q90: float, price: float) -> Optional[Dict]:
    """Forward-looking metrics from a single horizon's USD quantiles + price."""
    if price is None or price <= 0 or q50 is None:
        return None
    spread = (q90 - q10)
    metrics = {
        "expected_return": q50 / price - 1.0,
        "downside_return": q10 / price - 1.0,
        "upside_return": q90 / price - 1.0,
        "band_width": (spread / q50) if q50 else None,   # forecast uncertainty
        "skew": (((q90 - q50) - (q50 - q10)) / spread) if spread > 0 else 0.0,
    }
    if spread > 0:
        metrics["prob_up"] = float(min(1.0, max(0.0, (q90 - price) / spread)))
    else:
        metrics["prob_up"] = 1.0 if q50 >= price else 0.0
    return metrics


def to_brain_features(bands: Sequence[Dict], price: float,
                      horizon: str = DEFAULT_FORECAST_HORIZON) -> Dict[str, Optional[float]]:
    """Map TimesFM bands -> the two brain features (None when unavailable)."""
    out: Dict[str, Optional[float]] = {"tsfm_expected_return": None, "tsfm_band_width": None}
    b = bands_for_horizon(bands, horizon)
    if not b:
        return out
    m = forward_metrics(b.get("q10"), b.get("q50"), b.get("q90"), price)
    if not m:
        return out
    out["tsfm_expected_return"] = m["expected_return"]
    out["tsfm_band_width"] = m["band_width"]
    return out


def forecast_block(bands: Sequence[Dict], price: float,
                   model_version: Optional[str] = None,
                   horizon: str = DEFAULT_FORECAST_HORIZON) -> Optional[Dict]:
    """Full UI-facing time-series forecast block (None if no usable band)."""
    b = bands_for_horizon(bands, horizon)
    if not b:
        return None
    m = forward_metrics(b.get("q10"), b.get("q50"), b.get("q90"), price)
    if not m:
        return None
    return {
        "source": "timesfm",
        "model_version": model_version,
        "horizon": horizon,
        "price": round(float(price), 4),
        "q10": b.get("q10"), "q50": b.get("q50"), "q90": b.get("q90"),
        "expected_return": round(m["expected_return"], 4),
        "downside_return": round(m["downside_return"], 4),
        "upside_return": round(m["upside_return"], 4),
        "band_width": round(m["band_width"], 4) if m["band_width"] is not None else None,
        "skew": round(m["skew"], 4),
        "prob_up": round(m["prob_up"], 4),
    }
