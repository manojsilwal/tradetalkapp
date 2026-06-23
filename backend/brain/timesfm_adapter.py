"""Bridge from the existing TimesFM predictor to the brain's band format.

Keeps the brain importable with zero predictor dependencies (the predictor is
imported lazily, only when a live forecast is actually requested). The pure
conversion helper ``bands_from_response`` is offline-testable with a dict or a
``PredictorForecastResponse``.

Brain band format: ``[{"horizon": "63d", "q10": .., "q50": .., "q90": ..}, ...]``
mapping the predictor's ``HorizonBandUsd`` (q10_usd/q50_usd/q90_usd).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _get(obj: Any, key: str, default=None):
    """Attribute- or dict-style access (works for pydantic models and dicts)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _model_version(resp: Any) -> Optional[str]:
    mv = _get(resp, "model_version")
    if mv:
        return mv
    meta = _get(resp, "meta") or {}
    if isinstance(meta, dict):
        return meta.get("model_version") or meta.get("timesfm_model_version")
    return _get(meta, "model_version")


def bands_from_response(resp: Any) -> Tuple[List[Dict], Optional[str]]:
    """Convert a predictor response (object or dict) -> (bands, model_version)."""
    raw = _get(resp, "horizon_bands_usd") or []
    bands: List[Dict] = []
    for item in raw:
        q50 = _get(item, "q50_usd")
        if q50 is None:
            continue
        bands.append({
            "horizon": _get(item, "horizon"),
            "q10": _get(item, "q10_usd"),
            "q50": q50,
            "q90": _get(item, "q90_usd"),
        })
    return bands, _model_version(resp)


async def fetch_timesfm_bands(ticker: str, horizons: Optional[Sequence[str]] = None,
                              *, tool_registry: Any = None,
                              emit_ledger: bool = False
                              ) -> Tuple[List[Dict], Optional[str], str]:
    """Call the real TimesFM predictor and return (bands, model_version, status).

    Returns ``([], None, "unavailable")`` if the predictor cannot be imported
    (keeps the brain usable without the predictor installed/configured), and the
    predictor's own ``status`` (``ok``/``disabled``/``insufficient_data``/...)
    otherwise. Bands are only populated when status is ``ok``.
    """
    try:
        from ..predictor.agent import run_predictor_forecast
    except Exception:  # noqa: BLE001 - predictor optional
        return [], None, "unavailable"

    resp = await run_predictor_forecast(
        ticker, horizons=list(horizons) if horizons else ["1d", "5d", "21d", "63d"],
        tool_registry=tool_registry, emit_ledger=emit_ledger,
    )
    status = _get(resp, "status", "ok")
    if status != "ok":
        return [], _model_version(resp), status
    bands, mv = bands_from_response(resp)
    return bands, mv, status
