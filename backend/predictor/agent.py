"""Predictor orchestration — baselines, TimesFM mock/service, ensemble, synthesis."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from backend.swarm_reliability.schemas import EvidenceArtifact, stable_json_hash
from backend.swarm_reliability.schemas import parse_iso_datetime

from .baselines import drift_forecast, ewma_forecast, naive_forecast, seasonal_naive_forecast
from .config_loader import load_yaml_cached
from .conformal import apply_scale as conformal_apply_scale
from .conformal import load_scales as load_conformal_scales
from .features import default_as_of_date, snapshot_pit_factors
from .ensemble import weighted_inverse_mase
from .learned_weights import blend_weights, load_weights as load_learned_weights
from .kill_switch import predictor_baselines_only, predictor_enabled
from .ledger_emit import emit_predictor_decisions
from .manifest import build_manifest
from .schemas import HorizonBandUsd, PredictorForecastResponse
from .scenarios import bull_base_bear_3y_from_63d
from .synthesizer import synthesize_narrative
from .reviewer import review_narrative
from .timesfm_client import (
    MockTimesFMClient,
    fetch_timesfm_forecast_http,
    max_horizon_from_config,
)
from .timesfm_constants import DEFAULT_MODEL_LABEL, IDX_MEAN, IDX_Q10, IDX_Q50, IDX_Q90

logger = logging.getLogger(__name__)

HORIZON_TO_TD: Mapping[str, int] = {
    "1d": 1,
    "5d": 5,
    "21d": 21,
    "63d": 63,
}


def new_cycle_id(ticker: str, horizons: Sequence[str]) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag = "-".join(sorted({h.lower() for h in horizons}))
    return f"predictor-{ticker.upper()}-{day}-{tag}-{uuid.uuid4().hex[:6]}"


def _seed(ticker: str, cycle_id: str) -> int:
    h = hashlib.sha256(f"{ticker}:{cycle_id}".encode()).hexdigest()
    return int(h[:16], 16)


def _synthetic_level_prices(ticker: str, cycle_id: str, n: int = 512) -> np.ndarray:
    rng = np.random.default_rng(_seed(ticker, cycle_id))
    r = rng.normal(0.0004, 0.014, size=n)
    p = 100.0 * np.exp(np.cumsum(r))
    return p.astype(np.float64)


def _spot_from_series(series: np.ndarray) -> float:
    return float(series[-1]) if series.size else 100.0


def _load_price_series_from_data_lake(ticker: str, max_rows: int = 512) -> Optional[np.ndarray]:
    """Use daily close history when parquet exists under ``DATA_LAKE_DIR``."""
    try:
        import pandas as pd

        from backend.data_lake.config import DATA_LAKE_SOURCE, HF_DATASET_ID, PRICES_DIR

        path = os.path.join(PRICES_DIR, f"{ticker.upper()}.parquet")
        if not os.path.isfile(path) and DATA_LAKE_SOURCE == "hf" and HF_DATASET_ID:
            try:
                from huggingface_hub import hf_hub_download

                token = os.environ.get("HF_TOKEN")
                path = hf_hub_download(
                    repo_id=HF_DATASET_ID,
                    repo_type="dataset",
                    filename=f"daily_prices/{ticker.upper()}.parquet",
                    token=token,
                )
            except Exception:
                path = ""
        if not path or not os.path.isfile(path):
            return None
        df = pd.read_parquet(path, columns=["Close"])
        if df.empty or len(df) < 32:
            return None
        return df["Close"].tail(max_rows).astype(float).values
    except Exception as e:
        logger.debug("[Predictor] data lake prices unavailable %s: %s", ticker, e)
        return None


async def _macro_stale(tool_registry: Any) -> bool:
    thresholds = load_yaml_cached("predictor_thresholds.yaml")
    max_age_h = float(
        os.environ.get("PREDICTOR_MACRO_MAX_AGE_HOURS")
        or thresholds.get("stale_macro_hours")
        or 72.0,
    )
    stale = False
    try:
        macro_data = await tool_registry.invoke("macro_fetch", {}, timeout_s=45.0)
    except Exception:
        return False
    ind = macro_data.get("indicators") or {}
    macro_as_of = (
        ind.get("fred_fetched_at")
        or ind.get("as_of")
        or macro_data.get("as_of")
        or macro_data.get("fetched_at")
    )
    dt = parse_iso_datetime(str(macro_as_of) if macro_as_of else None)
    if dt is not None:
        from datetime import datetime as _dt, timezone as _tz

        age_h = (_dt.now(_tz.utc) - dt).total_seconds() / 3600.0
        stale = age_h > max_age_h
    elif os.environ.get("PREDICTOR_REQUIRE_MACRO_ASOF", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        stale = True
    return stale


def _direction_from_prices(spot: float, q50: float, eps: float = 1e-4) -> str:
    if spot <= 0 or q50 <= 0:
        return "flat"
    r = (q50 - spot) / spot
    if r > eps:
        return "up"
    if r < -eps:
        return "down"
    return "flat"


async def run_predictor_forecast(
    ticker: str,
    horizons: Optional[List[str]] = None,
    *,
    tool_registry: Optional[Any] = None,
    emit_ledger: bool = True,
) -> PredictorForecastResponse:
    from backend.decision_terminal import DISCLAIMER as DT_DISCLAIMER

    disclaimer = f"{DT_DISCLAIMER} Forecasts are probabilistic and frequently wrong."

    hs = list(horizons or ["1d", "5d", "21d", "63d"])
    if not predictor_enabled():
        return PredictorForecastResponse(
            status="disabled",
            ticker=ticker.upper(),
            cycle_id=new_cycle_id(ticker, hs),
            disclaimer=disclaimer,
            executed=False,
            assumptions=["Predictor disabled via PREDICTOR_ENABLE or PREDICTOR_BACKEND."],
        )

    cycle_id = new_cycle_id(ticker, hs)
    t = ticker.upper()

    if tool_registry is not None:
        gate = os.environ.get("PREDICTOR_STALE_GATE", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if gate and await _macro_stale(tool_registry):
            return PredictorForecastResponse(
                status="stale_data",
                ticker=t,
                cycle_id=cycle_id,
                disclaimer=disclaimer,
                executed=False,
                blocked_until_freshness_gate_passes=True,
                assumptions=["Macro evidence older than configured threshold."],
            )

    use_lake = os.environ.get("PREDICTOR_USE_DATA_LAKE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    lake_series = _load_price_series_from_data_lake(t) if use_lake else None
    if lake_series is not None and lake_series.size >= 64:
        series = lake_series
        price_source = "data_lake_daily_close"
    else:
        series = _synthetic_level_prices(t, cycle_id)
        price_source = "synthetic"
    spot = _spot_from_series(series)
    log_p = np.log(np.maximum(series, 1e-8))

    as_of_d = default_as_of_date()
    pit_factors = snapshot_pit_factors(t, as_of_d)

    max_h = max_horizon_from_config()
    mock_client = MockTimesFMClient(
        model_version=DEFAULT_MODEL_LABEL if predictor_baselines_only() else DEFAULT_MODEL_LABEL,
    )
    path = mock_client.forecast_price_path(
        log_p, max_h, ticker=t, cycle_id=cycle_id
    )

    cfg_payload = {
        "timesfm": load_yaml_cached("timesfm_forecast_config.yaml"),
        "thresholds": load_yaml_cached("predictor_thresholds.yaml"),
    }
    config_hash = stable_json_hash(cfg_payload)
    input_payload = {"ticker": t, "cycle_id": cycle_id, "horizons": hs, "spot": round(spot, 6)}
    input_hash = stable_json_hash(input_payload)

    shadow_logged = False
    model_label = DEFAULT_MODEL_LABEL
    forecast_source = "mock"
    remote_primary = os.environ.get("TIMESFM_REMOTE_PRIMARY", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if (os.environ.get("TIMESFM_SERVICE_URL") or "").strip() and not predictor_baselines_only():
        try:
            remote = await fetch_timesfm_forecast_http(
                inputs=log_p[-1024:].tolist(),
                horizon=min(64, max_h),
                config_hash=config_hash,
                model_version=DEFAULT_MODEL_LABEL,
            )
            if remote:
                from .timesfm_client import http_quantiles_to_numpy_path

                remote_arr = http_quantiles_to_numpy_path(remote)
                # Remote-primary (Phase 2): when the deployed TimesFM service
                # answers with a usable quantile path, it IS the forecast.
                # The deterministic mock stays as fallback + shadow reference.
                if remote_primary and remote_arr is not None and remote_arr.shape[0] >= 1:
                    needed = min(64, max_h)
                    if remote_arr.shape[0] >= min(needed, max(HORIZON_TO_TD.values())):
                        path = remote_arr
                        model_label = str(remote.get("model_version") or DEFAULT_MODEL_LABEL)
                        forecast_source = "timesfm_service"
                try:
                    from backend.coral_hub import log_handoff_event
                    from backend.coral_dreaming import EVENT_PREDICTOR_SHADOW

                    mock_row = mock_client.forecast_price_path(
                        log_p, min(64, max_h), ticker=t, cycle_id=cycle_id
                    )
                    diff_note = "shape_mismatch"
                    if remote_arr is not None and remote_arr.shape == mock_row.shape:
                        diff = float(np.max(np.abs(remote_arr - mock_row)))
                        diff_note = f"max_abs_diff={diff:.6f}"

                    log_handoff_event(
                        EVENT_PREDICTOR_SHADOW,
                        {
                            "ticker": t,
                            "cycle_id": cycle_id,
                            "remote_keys": list(remote.keys()),
                            "compare": diff_note,
                            "remote_primary": forecast_source == "timesfm_service",
                        },
                    )
                    shadow_logged = True
                except Exception:
                    pass
        except Exception as e:
            logger.debug("[Predictor] remote fetch skipped: %s", e)

    bands: List[HorizonBandUsd] = []
    ensemble_weights_last: Dict[str, float] = {}

    # Self-learning artifacts (Phase 3) — both no-ops until their nightly
    # jobs have committed a first version to the resource registry.
    conformal_scales = load_conformal_scales()
    learned_wts_by_h = load_learned_weights()
    conformal_applied: Dict[str, float] = {}

    for h_label in hs:
        td = HORIZON_TO_TD.get(h_label, 21)
        td = min(td, path.shape[0])
        row = path[td - 1]
        q10_l = float(row[IDX_Q10])
        q50_l = float(row[IDX_Q50])
        q90_l = float(row[IDX_Q90])
        mean_l = float(row[IDX_MEAN])

        q10_usd = math.exp(q10_l)
        q50_usd = math.exp(q50_l)
        q90_usd = math.exp(q90_l)
        point_usd = math.exp(mean_l)

        naive_p = naive_forecast(series, td)
        sn_p = seasonal_naive_forecast(series, td)
        ew_p = ewma_forecast(series, td)
        dr_p = drift_forecast(series, td)
        members = {
            "naive": naive_p,
            "seasonal_naive": sn_p,
            "ewma": ew_p,
            "drift": dr_p,
            "timesfm_mean": point_usd,
        }
        blended, wts = weighted_inverse_mase(series, td, members)
        learned = learned_wts_by_h.get(h_label) or {}
        if learned:
            wts = blend_weights(wts, learned)
            blended = sum(wts[k] * members[k] for k in wts)
        ensemble_weights_last = dict(wts)

        lo_usd = min(q10_usd, q50_usd, q90_usd)
        hi_usd = max(q10_usd, q50_usd, q90_usd)
        scale = conformal_scales.get(h_label)
        if scale is not None:
            lo_usd, _, hi_usd = conformal_apply_scale(lo_usd, q50_usd, hi_usd, scale)
            conformal_applied[h_label] = scale

        bands.append(
            HorizonBandUsd(
                horizon=h_label,
                q10_usd=lo_usd,
                q50_usd=q50_usd,
                q90_usd=hi_usd,
                point_usd=blended,
            )
        )

    # Direction from longest horizon q50
    long_h = max(hs, key=lambda x: HORIZON_TO_TD.get(x, 0))
    long_td = HORIZON_TO_TD.get(long_h, 63)
    long_td = min(long_td, path.shape[0])
    row63 = path[long_td - 1]
    q50_star = math.exp(float(row63[IDX_Q50]))
    directional = _direction_from_prices(spot, q50_star)

    bull_3y, base_3y, bear_3y = bull_base_bear_3y_from_63d(
        spot,
        math.exp(float(row63[IDX_Q10])),
        q50_star,
        math.exp(float(row63[IDX_Q90])),
        horizon_days=long_td,
    )

    tool_json: Dict[str, Any] = {
        "ticker": t,
        "spot_usd": spot,
        "horizons": [b.model_dump() for b in bands],
        "directional_bias": directional,
        "model_version": model_label,
        "pit_factors": pit_factors,
        "price_source": price_source,
        "forecast_source": forecast_source,
    }
    syn_task = synthesize_narrative(tool_json=tool_json, cycle_id=cycle_id)
    syn_text = await syn_task
    rev_text = await review_narrative(synthesis_text=syn_text, tool_json=tool_json)

    price_art = [
        EvidenceArtifact(
            artifact_id=f"{cycle_id}-prices",
            source=f"predictor_{price_source}",
            as_of=datetime.now(timezone.utc).isoformat(),
            metadata={"ticker": t, "pit_factors": pit_factors},
        )
    ]
    manifest = build_manifest(cycle_id=cycle_id, price_artifacts=price_art)

    resp = PredictorForecastResponse(
        status="ok",
        ticker=t,
        cycle_id=cycle_id,
        disclaimer=disclaimer,
        model_version=model_label,
        model_confidence="medium",
        directional_bias=directional,  # type: ignore[arg-type]
        horizon_bands_usd=bands,
        bull_price_usd_3y_scenario=bull_3y,
        base_price_usd_3y_scenario=base_3y,
        bear_price_usd_3y_scenario=bear_3y,
        assumptions=[
            "80 % interval uses q10–q90 from the probabilistic head (mock or service).",
            "3Y scenario prices extrapolate geometrically from the longest configured horizon.",
            f"Price history source: {price_source}.",
        ],
        synthesis_summary=syn_text[:1200],
        reviewer_summary=rev_text[:800],
        executed=True,
        ensemble_weights={k: round(v, 6) for k, v in ensemble_weights_last.items()},
        input_hash=input_hash,
        config_hash=config_hash,
        shadow_diff_logged=shadow_logged,
        meta={
            "manifest": manifest.model_dump(mode="json"),
            "pit_factors": pit_factors,
            "price_source": price_source,
            "forecast_source": forecast_source,
            "conformal_scales": conformal_applied,
            "price_evidence_chunks": [
                {
                    "chunk_id": f"{cycle_id}-prices",
                    "collection": "predictor_prices",
                    "relevance": 1.0,
                    "rank": 0,
                }
            ],
        },
    )

    try:
        from backend.coral_hub import log_handoff_event
        from backend.coral_dreaming import EVENT_PREDICTOR

        log_handoff_event(
            EVENT_PREDICTOR,
            {"ticker": t, "cycle_id": cycle_id, "model": resp.model_version},
        )
    except Exception:
        pass

    if emit_ledger and os.environ.get("DECISION_LEDGER_ENABLE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        emit_predictor_decisions(
            ticker=t,
            resp=resp,
            horizons=hs,
            inputs_hash=input_hash,
            config_hash=config_hash,
        )

    return resp

