"""Serve ``/predictor/forecast`` from brain snapshot TimesFM bands.

When ``BRAIN_CUTOVER_PREDICTOR=1``, the predictor surface reads the nightly
brain snapshot's ``timesfm_bands`` instead of calling the legacy predictor
agent. Bands are absolute USD anchors; live spot is used for directional bias
and 3Y scenario extrapolation (same contract as the legacy predictor).
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from ..predictor.agent import new_cycle_id
from ..predictor.schemas import HorizonBandUsd, PredictorForecastResponse
from ..predictor.scenarios import bull_base_bear_3y_from_63d
from . import timeseries as ts
from .ports.factory import get_storage
from .serving import _latest_as_of, _live_price
from .snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)


def _disclaimer() -> str:
    from ..decision_terminal import DISCLAIMER as DT_DISCLAIMER

    return f"{DT_DISCLAIMER} Forecasts are probabilistic and frequently wrong."


def _direction_from_prices(spot: float, q50: float, eps: float = 1e-4) -> str:
    if spot <= 0 or q50 <= 0:
        return "flat"
    move = (q50 - spot) / spot
    if move > eps:
        return "up"
    if move < -eps:
        return "down"
    return "flat"


def _aggregate_direction(spot: float, bands_raw: List[dict], horizons: List[str]) -> str:
    dirs = set()
    for h in horizons:
        band = ts.bands_for_horizon(bands_raw, h)
        if not band or band.get("q50") is None:
            continue
        dirs.add(_direction_from_prices(spot, float(band["q50"])))
    if not dirs:
        return "flat"
    if len(dirs) == 1:
        return next(iter(dirs))
    if dirs == {"up", "down"}:
        return "mixed"
    return "mixed" if "mixed" in dirs else "flat"


def run_brain_predictor_forecast(
    ticker: str,
    horizons: Optional[List[str]] = None,
) -> PredictorForecastResponse:
    """Build a predictor response from the latest brain snapshot (sync)."""
    hs = [h.strip().lower() for h in (horizons or ["1d", "5d", "21d", "63d"]) if h.strip()]
    sym = (ticker or "").upper().strip()
    cycle_id = new_cycle_id(sym, hs)
    disclaimer = _disclaimer()

    if not sym:
        return PredictorForecastResponse(
            status="insufficient_data",
            ticker="",
            cycle_id=cycle_id,
            disclaimer=disclaimer,
            executed=False,
            assumptions=["empty ticker"],
        )

    storage = get_storage()
    store = SnapshotStore(storage=storage)
    as_of = _latest_as_of(store, sym)
    if not as_of or not store.exists(sym, as_of):
        return PredictorForecastResponse(
            status="insufficient_data",
            ticker=sym,
            cycle_id=cycle_id,
            disclaimer=disclaimer,
            executed=False,
            assumptions=["no brain snapshot available; run the brain-nightly job"],
        )

    snapshot = store.load(sym, as_of)
    bands_raw = list(snapshot.timesfm_bands or [])
    if not bands_raw:
        return PredictorForecastResponse(
            status="insufficient_data",
            ticker=sym,
            cycle_id=cycle_id,
            disclaimer=disclaimer,
            executed=False,
            assumptions=[
                "No TimesFM bands in brain snapshot; run brain-nightly with BRAIN_TIMESFM_ENABLE=1",
            ],
            meta={"as_of_date": snapshot.as_of_date, "source": "brain_snapshot"},
        )

    spot, price_source = _live_price(sym)
    if spot is None:
        spot = snapshot.base_price
        price_source = "snapshot_base"

    horizon_bands: List[HorizonBandUsd] = []
    for h in hs:
        band = ts.bands_for_horizon(bands_raw, h)
        if not band:
            continue
        q50 = band.get("q50")
        if q50 is None:
            continue
        horizon_bands.append(
            HorizonBandUsd(
                horizon=h,
                q10_usd=band.get("q10"),
                q50_usd=q50,
                q90_usd=band.get("q90"),
                point_usd=q50,
            )
        )

    if not horizon_bands:
        return PredictorForecastResponse(
            status="insufficient_data",
            ticker=sym,
            cycle_id=cycle_id,
            disclaimer=disclaimer,
            executed=False,
            assumptions=[f"No TimesFM bands for requested horizons: {', '.join(hs)}"],
            meta={"as_of_date": snapshot.as_of_date, "source": "brain_snapshot"},
        )

    b63 = ts.bands_for_horizon(bands_raw, "63d")
    bull_p = base_p = bear_p = None
    if b63 and spot and spot > 0:
        bull_p, base_p, bear_p = bull_base_bear_3y_from_63d(
            float(spot),
            float(b63.get("q10") or spot),
            float(b63.get("q50") or spot),
            float(b63.get("q90") or spot),
        )

    ts_block = ts.forecast_block(bands_raw, float(spot), snapshot.timesfm_model_version)
    synthesis = ""
    if ts_block:
        er = ts_block.get("expected_return")
        if er is not None:
            synthesis = (
                f"Brain time-series ({ts_block.get('horizon', '63d')}): "
                f"expected return {float(er):+.1%} vs spot ${float(spot):.2f} "
                f"({price_source})."
            )

    model_version = snapshot.timesfm_model_version or f"{snapshot.model_name}-{snapshot.model_version}"

    return PredictorForecastResponse(
        status="ok",
        ticker=sym,
        cycle_id=cycle_id,
        disclaimer=disclaimer,
        model_version=model_version,
        model_confidence="medium",
        directional_bias=_aggregate_direction(float(spot), bands_raw, hs),  # type: ignore[arg-type]
        horizon_bands_usd=horizon_bands,
        bull_price_usd_3y_scenario=bull_p,
        base_price_usd_3y_scenario=base_p,
        bear_price_usd_3y_scenario=bear_p,
        assumptions=[
            "Forecast served from finance-brain nightly snapshot TimesFM bands.",
            f"Snapshot as_of={snapshot.as_of_date}; spot source={price_source}.",
        ],
        synthesis_summary=synthesis,
        reviewer_summary="",
        executed=True,
        meta={
            "source": "brain_snapshot",
            "as_of_date": snapshot.as_of_date,
            "price_source": price_source,
            "brain_model": f"{snapshot.model_name}-{snapshot.model_version}",
        },
    )


async def arun_brain_predictor_forecast(
    ticker: str,
    horizons: Optional[List[str]] = None,
) -> PredictorForecastResponse:
    """Async wrapper for event-loop callers."""
    return await asyncio.to_thread(run_brain_predictor_forecast, ticker, horizons)
