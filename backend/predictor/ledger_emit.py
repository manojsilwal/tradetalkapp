"""Decision-outcome ledger emits for price forecasts."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Sequence

from backend import decision_ledger as dl
from backend.predictor.schemas import HorizonBandUsd, PredictorForecastResponse

logger = logging.getLogger(__name__)


def _registry_snapshot() -> str:
    from backend.decision_ledger_registry import registry_attribution

    return registry_attribution()[1]


def _prompt_versions_dict() -> Dict[str, str]:
    from backend.decision_ledger_registry import registry_attribution

    return registry_attribution()[0]


def _verdict_for_grader(directional: str) -> str:
    u = (directional or "").lower().strip()
    if u == "up":
        return "UP"
    if u == "down":
        return "DOWN"
    if u in ("flat", "mixed"):
        return u.upper()
    return "FLAT"


def emit_predictor_decisions(
    *,
    ticker: str,
    resp: PredictorForecastResponse,
    horizons: Sequence[str],
    inputs_hash: str,
    config_hash: str,
    source_route: str = "backend/predictor/agent.py",
) -> None:
    if resp.status != "ok" or not resp.executed:
        return

    evidence = []
    for art in resp.meta.get("price_evidence_chunks") or []:
        try:
            evidence.append(
                dl.EvidenceRef(
                    chunk_id=str(art.get("chunk_id") or ""),
                    collection=str(art.get("collection") or "prices"),
                    relevance=float(art["relevance"]) if art.get("relevance") is not None else None,
                    rank=int(art.get("rank") or 0),
                )
            )
        except Exception:
            continue

    features_common = [
        dl.FeatureValue(name="model_confidence", value_str=resp.model_confidence),
        dl.FeatureValue(name="model_version", value_str=resp.model_version),
        dl.FeatureValue(name="config_hash", value_str=config_hash),
        dl.FeatureValue(name="input_hash", value_str=inputs_hash),
        dl.FeatureValue(
            name="ensemble_weights_json",
            value_str=json.dumps(resp.ensemble_weights, sort_keys=True),
        ),
        dl.FeatureValue(
            name="forecast_source",
            value_str=str(resp.meta.get("forecast_source") or "mock"),
        ),
    ]
    conformal_scales = resp.meta.get("conformal_scales") or {}

    snap = _registry_snapshot()
    pv_dict = _prompt_versions_dict()

    base_id = dl.new_decision_id()

    by_h: Dict[str, HorizonBandUsd] = {b.horizon: b for b in resp.horizon_bands_usd}

    for h in horizons:
        band = by_h.get(h)
        point = band.point_usd if band else None
        out: Dict[str, Any] = {
            "cycle_id": resp.cycle_id,
            "predictor_status": resp.status,
            "point_forecast_usd": point,
            # Quantile band in USD so the outcome grader can score pinball
            # loss and q10–q90 coverage against the realized close at T+H.
            "q10_usd": band.q10_usd if band else None,
            "q50_usd": band.q50_usd if band else None,
            "q90_usd": band.q90_usd if band else None,
            "directional_bias": resp.directional_bias,
            "horizon": h,
            "ticker": ticker.upper(),
            "synthesis_summary": resp.synthesis_summary[:1200],
            "reviewer_summary": resp.reviewer_summary[:1200],
        }
        verdict_graded = _verdict_for_grader(resp.directional_bias)
        did = f"{base_id}:{h}"
        features_h = list(features_common)
        if conformal_scales.get(h) is not None:
            try:
                features_h.append(
                    dl.FeatureValue(
                        name="conformal_scale", value_num=float(conformal_scales[h]),
                    )
                )
            except (TypeError, ValueError):
                pass
        dl.emit_decision(
            decision_type="price_forecast",
            symbol=ticker,
            horizon_hint=h,
            verdict=verdict_graded,
            confidence=None,
            inputs_hash=inputs_hash,
            output=out,
            source_route=source_route,
            evidence=evidence,
            features=features_h,
            decision_id=did,
            model=resp.model_version,
            prompt_versions=pv_dict,
            registry_snapshot_id=snap,
        )
