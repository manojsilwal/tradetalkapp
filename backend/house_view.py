"""
House View — fused Super Investor verdict (Phase 4).

One position per ticker, built from everything the harness already grades:

* the predictor's calibrated quantile bands (numeric forecast),
* recent ledger verdicts for the symbol (swarm factors, debate, decision
  terminal, scorecard — the LLM side of the house),
* the prevailing market regime recorded in ``feature_snapshots``.

The fusion is deliberately simple, transparent math — a weighted directional
score — because the moat is the *grading* of this surface, not the cleverness
of the blend: every House View emits to the Decision-Outcome Ledger
(per AGENTS.md §Decision-Outcome Ledger rule) and is scored by the nightly
grader, so the blend weights can later be tuned against measured hit rates.

Kill switch: ``HOUSE_VIEW_ENABLE=0`` → router returns 503; this module stays
importable.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from . import decision_ledger as dl

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")

_VERDICT_SCORE = {
    "STRONG BUY": 1.0,
    "BUY": 0.6,
    "UP": 0.6,
    "HOLD": 0.0,
    "NEUTRAL": 0.0,
    "FLAT": 0.0,
    "MIXED": 0.0,
    "SELL": -0.6,
    "DOWN": -0.6,
    "STRONG SELL": -1.0,
}

_RECENT_TYPES = ("swarm_factor", "debate", "decision_terminal", "scorecard")
DEFAULT_HORIZON = "21d"
FORECAST_WEIGHT = 0.5  # numeric model vs LLM-verdict consensus


def house_view_enabled() -> bool:
    return (os.getenv("HOUSE_VIEW_ENABLE", "1").strip().lower() or "1") in _TRUTHY


def _recent_symbol_verdicts(
    symbol: str, *, since_days: float = 7.0, limit: int = 200,
) -> List[Dict[str, Any]]:
    try:
        ledger = dl.get_ledger()
        events = ledger.list_decisions_since(
            time.time() - since_days * 86400.0, limit=limit,
        )
    except Exception:
        return []
    sym = symbol.upper()
    out = []
    for ev in events:
        if (ev.symbol or "").upper() != sym:
            continue
        if ev.decision_type not in _RECENT_TYPES:
            continue
        if not (ev.verdict or "").strip():
            continue
        out.append(
            {
                "decision_type": ev.decision_type,
                "verdict": ev.verdict.upper().strip(),
                "confidence": ev.confidence,
                "created_at": ev.created_at,
            }
        )
    return out


def _recent_regime(symbol: str) -> str:
    try:
        ledger = dl.get_ledger()
        conn = ledger._conn()  # type: ignore[attr-defined]
        if conn is None:
            return ""
        row = conn.execute(
            """SELECT f.regime FROM feature_snapshots f
               JOIN decision_events d ON d.decision_id = f.decision_id
               WHERE f.regime != '' AND (d.symbol = ? OR d.symbol = '')
               ORDER BY d.created_at DESC LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        return str(row["regime"]) if row else ""
    except Exception:
        return ""


def _fuse(
    forecast_dir_score: float,
    band_width_rel: Optional[float],
    verdicts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    verdict_scores = [
        _VERDICT_SCORE.get(v["verdict"], 0.0) for v in verdicts
    ]
    consensus = sum(verdict_scores) / len(verdict_scores) if verdict_scores else 0.0
    agreement = 0.0
    if verdict_scores:
        same_sign = sum(
            1 for s in verdict_scores
            if (s > 0) == (consensus > 0) and abs(s) > 1e-9
        )
        agreement = same_sign / len(verdict_scores)

    score = FORECAST_WEIGHT * forecast_dir_score + (1.0 - FORECAST_WEIGHT) * consensus

    if score >= 0.5:
        verdict = "BUY"
    elif score >= 0.15:
        verdict = "HOLD"  # constructive but below conviction bar
    elif score <= -0.5:
        verdict = "SELL"
    elif score <= -0.15:
        verdict = "HOLD"
    else:
        verdict = "HOLD"

    # Confidence: agreement between sides, penalised by band width (wide
    # band = model itself is unsure).
    conf = 0.5 + 0.5 * min(1.0, abs(score))
    if verdicts:
        conf = 0.6 * conf + 0.4 * agreement
    if band_width_rel is not None:
        conf *= max(0.4, 1.0 - min(0.6, band_width_rel))
    conf = round(max(0.05, min(0.95, conf)), 3)

    # Position sizing hint: conviction-scaled, capped at 5 % of portfolio.
    position_pct = round(5.0 * abs(score) * conf, 2) if verdict != "HOLD" else 0.0

    return {
        "verdict": verdict,
        "score": round(score, 4),
        "confidence": conf,
        "consensus_score": round(consensus, 4),
        "agreement_ratio": round(agreement, 3),
        "suggested_position_pct": position_pct,
    }


async def build_house_view(
    ticker: str,
    *,
    horizon: str = DEFAULT_HORIZON,
    knowledge_store: Optional[Any] = None,
    emit_ledger: bool = True,
) -> Dict[str, Any]:
    """Compose forecast + verdict consensus into one graded recommendation."""
    from .predictor.agent import run_predictor_forecast

    t = ticker.upper().strip()

    # Numeric side — predictor emits its own price_forecast decisions; the
    # house view is a separate decision so we don't double-emit forecasts.
    forecast = await run_predictor_forecast(
        t, horizons=[horizon], tool_registry=None, emit_ledger=False,
    )
    forecast_dir_score = 0.0
    band: Dict[str, Any] = {}
    band_width_rel: Optional[float] = None
    if forecast.status == "ok" and forecast.horizon_bands_usd:
        b = forecast.horizon_bands_usd[0]
        band = {
            "q10_usd": b.q10_usd,
            "q50_usd": b.q50_usd,
            "q90_usd": b.q90_usd,
            "point_usd": b.point_usd,
        }
        bias = (forecast.directional_bias or "flat").lower()
        forecast_dir_score = {"up": 0.8, "down": -0.8}.get(bias, 0.0)
        if b.q10_usd and b.q90_usd and b.q50_usd:
            band_width_rel = (b.q90_usd - b.q10_usd) / max(1e-8, b.q50_usd)

    # LLM-consensus side.
    verdicts = _recent_symbol_verdicts(t)
    regime = _recent_regime(t)

    fused = _fuse(forecast_dir_score, band_width_rel, verdicts)

    # RAG evidence — query_with_refs threads chunk ids into ledger evidence.
    evidence_refs: List[dl.EvidenceRef] = []
    evidence_docs: List[str] = []
    if knowledge_store is not None:
        try:
            docs, refs = knowledge_store.query_with_refs(
                "swarm_history", f"{t} investment analysis verdict", n_results=3,
            )
            evidence_docs = [str(d)[:280] for d in (docs or [])]
            for r in refs or []:
                try:
                    dist = r.get("distance")
                    evidence_refs.append(
                        dl.EvidenceRef(
                            chunk_id=str(r.get("chunk_id") or ""),
                            collection=str(r.get("collection") or "swarm_history"),
                            relevance=(1.0 - float(dist)) if dist is not None else None,
                            rank=int(r.get("rank") or 0),
                        )
                    )
                except Exception:
                    continue
        except Exception as e:
            logger.debug("[HouseView] RAG evidence skipped: %s", e)

    payload: Dict[str, Any] = {
        "ticker": t,
        "horizon": horizon,
        "verdict": fused["verdict"],
        "confidence": fused["confidence"],
        "score": fused["score"],
        "suggested_position_pct": fused["suggested_position_pct"],
        "forecast": {
            "status": forecast.status,
            "directional_bias": forecast.directional_bias,
            "model_version": forecast.model_version,
            "forecast_source": str(forecast.meta.get("forecast_source") or ""),
            "band": band,
            "band_width_rel": round(band_width_rel, 4) if band_width_rel is not None else None,
        },
        "consensus": {
            "n_recent_verdicts": len(verdicts),
            "consensus_score": fused["consensus_score"],
            "agreement_ratio": fused["agreement_ratio"],
            "recent_verdicts": verdicts[:10],
        },
        "market_regime": regime,
        "evidence_previews": evidence_docs,
        "disclaimer": (
            "House View is informational analysis combining model forecasts and "
            "agent consensus. Not investment advice."
        ),
        "generated_at": time.time(),
    }

    if emit_ledger:
        _emit_house_view_decision(payload, evidence_refs, band_width_rel)

    return payload


def _emit_house_view_decision(
    payload: Dict[str, Any],
    evidence: List[dl.EvidenceRef],
    band_width_rel: Optional[float],
) -> None:
    """Ledger emit — failure must never break the user-facing response."""
    try:
        from .decision_ledger_registry import registry_attribution

        pv, snap, model = registry_attribution()
        features = [
            dl.FeatureValue(name="market_regime", value_str=payload.get("market_regime") or ""),
            dl.FeatureValue(
                name="forecast_direction",
                value_str=str((payload.get("forecast") or {}).get("directional_bias") or ""),
            ),
            dl.FeatureValue(
                name="forecast_source",
                value_str=str((payload.get("forecast") or {}).get("forecast_source") or ""),
            ),
            dl.FeatureValue(
                name="n_recent_verdicts",
                value_num=float((payload.get("consensus") or {}).get("n_recent_verdicts") or 0),
            ),
            dl.FeatureValue(
                name="agreement_ratio",
                value_num=float((payload.get("consensus") or {}).get("agreement_ratio") or 0.0),
            ),
        ]
        if band_width_rel is not None:
            features.append(
                dl.FeatureValue(name="band_width_rel", value_num=float(band_width_rel))
            )
        dl.emit_decision(
            decision_type="house_view",
            symbol=payload["ticker"],
            horizon_hint=payload.get("horizon") or DEFAULT_HORIZON,
            verdict=payload["verdict"],
            confidence=payload.get("confidence"),
            inputs_hash="",
            output={
                "score": payload.get("score"),
                "suggested_position_pct": payload.get("suggested_position_pct"),
                "forecast": payload.get("forecast"),
                "consensus_score": (payload.get("consensus") or {}).get("consensus_score"),
            },
            source_route="backend/house_view.py",
            evidence=evidence,
            features=features,
            decision_id=dl.new_decision_id(),
            model=model,
            prompt_versions=pv,
            registry_snapshot_id=snap,
        )
    except Exception as e:
        logger.warning("[HouseView] ledger emit failed (non-fatal): %s", e)
