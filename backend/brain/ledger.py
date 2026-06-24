"""Decision-Outcome Ledger producer for the finance brain.

Builds the ``emit_fn`` that ``ReflexEngine`` calls with the live-adjusted result
the user actually saw, and writes it to the model-agnostic ledger as
``decision_type="brain_verdict"`` with feature values, optional RAG evidence,
and prompt/registry attribution. Always best-effort — ledger failure must never
break serving (AGENTS.md).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_HORIZON_MAP = {1: "1d", 5: "5d", 21: "21d", 63: "63d"}


def _horizon_hint(horizon_days: Optional[int]) -> str:
    return _HORIZON_MAP.get(int(horizon_days), "none") if horizon_days else "none"


def _registry_stamps() -> "tuple[Dict[str, str], str]":
    try:
        from .. import resource_registry as rr
        return dict(rr.list_active() or {}), str(rr.snapshot_id() or "")
    except Exception:  # noqa: BLE001
        return {}, ""


def _evidence_for(ticker: str, knowledge_store: Any):
    if knowledge_store is None:
        return []
    try:
        from ..decision_ledger import EvidenceRef
        hits = knowledge_store.query_with_refs(f"{ticker} outlook valuation risk", n_results=4)
        refs: List[Any] = []
        for rank, h in enumerate(hits or []):
            refs.append(EvidenceRef(
                chunk_id=str(h.get("chunk_id") or h.get("id") or ""),
                collection=str(h.get("collection") or ""),
                relevance=h.get("relevance"),
                rank=rank,
            ))
        return [r for r in refs if r.chunk_id]
    except Exception as e:  # noqa: BLE001
        logger.debug("[brain.ledger] evidence fetch skipped: %s", e)
        return []


def _features_from(result: Dict) -> List[Any]:
    from ..decision_ledger import FeatureValue
    block = result.get("live") or result.get("base") or {}
    feats: List[Any] = []
    op = block.get("outperform_probability")
    if op is not None:
        feats.append(FeatureValue(name="outperform_probability", value_num=float(op)))
    risk = block.get("risk_score")
    if risk is not None:
        feats.append(FeatureValue(name="risk_score", value_num=float(risk)))
    for group, score in (block.get("signal_scores") or {}).items():
        if score is not None:
            feats.append(FeatureValue(name=f"signal_{group}", value_num=float(score)))
    recon = result.get("reconciliation") or {}
    if recon.get("quadrant"):
        feats.append(FeatureValue(name="reconciliation_quadrant", value_str=str(recon["quadrant"])))
    return feats


def build_emit_fn(*, user_id: str = "", source_route: str = "/brain/ticker",
                  knowledge_store: Any = None) -> Callable[[Dict], None]:
    """Return an emit_fn(result_dict) suitable for ReflexEngine(emit_fn=...)."""

    def _emit(result: Dict) -> None:
        try:
            from .. import decision_ledger as dl
            block = result.get("live") or result.get("base") or {}
            verdict = block.get("recommendation") or (result.get("base") or {}).get("recommendation", "")
            horizon_days = (result.get("base") or {}).get("horizon_days")
            prompt_versions, registry_snapshot = _registry_stamps()
            dl.emit_decision(
                decision_type="brain_verdict",
                symbol=result.get("ticker", ""),
                user_id=user_id,
                horizon_hint=_horizon_hint(horizon_days),
                model=result.get("model_version", ""),
                verdict=str(verdict),
                confidence=result.get("confidence_score"),
                source_route=source_route,
                output={
                    "status": result.get("status"),
                    "recommendation": verdict,
                    "outperform_probability": block.get("outperform_probability"),
                    "composite_score": block.get("composite_score"),
                    "risk_score": block.get("risk_score"),
                    "valuation": result.get("valuation"),
                    "reconciliation": result.get("reconciliation"),
                },
                prompt_versions=prompt_versions,
                registry_snapshot_id=registry_snapshot,
                evidence=_evidence_for(result.get("ticker", ""), knowledge_store),
                features=_features_from(result),
            )
        except Exception as e:  # noqa: BLE001 - never break serving
            logger.warning("[brain.ledger] emit failed: %s", e)

    return _emit
