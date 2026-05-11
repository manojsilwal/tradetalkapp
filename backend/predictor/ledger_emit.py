"""Decision-outcome ledger emits for price forecasts."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Sequence

from backend import decision_ledger as dl
from backend.predictor.schemas import HorizonBandUsd, PredictorForecastResponse

logger = logging.getLogger(__name__)


def _registry_snapshot() -> str:
    try:
        from backend.resource_registry import get_resource_registry, registry_enabled

        if registry_enabled():
            return get_resource_registry().snapshot_id()
    except Exception:
        pass
    return ""


def _prompt_versions_dict() -> Dict[str, str]:
    try:
        from backend.resource_registry import get_resource_registry, registry_enabled

        if not registry_enabled():
            return {}
        reg = get_resource_registry()
        return {r.name: r.version for r in reg.list()}
    except Exception:
        return {}


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
    ]

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
            "directional_bias": resp.directional_bias,
            "horizon": h,
            "ticker": ticker.upper(),
            "synthesis_summary": resp.synthesis_summary[:1200],
            "reviewer_summary": resp.reviewer_summary[:1200],
        }
        verdict_graded = _verdict_for_grader(resp.directional_bias)
        did = f"{base_id}:{h}"
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
            features=features_common,
            decision_id=did,
            model=resp.model_version,
            prompt_versions=pv_dict,
            registry_snapshot_id=snap,
        )
