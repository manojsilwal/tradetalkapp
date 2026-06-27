"""
Decision-Outcome Ledger emit for the Narrative Rotation Radar.

Per AGENTS.md (Harness Engineering Phase 2): every user-facing surface that
produces a verdict MUST emit to the ledger before returning. This mirrors
``backend/picks_shovels/ledger.py`` — one ``decision_type="theme_phase"`` row per
theme, with the top-level scores as features and the lifecycle phase as the
verdict. Wrapped so a ledger failure can never break the scan.

Because the outcome_grader scores any ledger row by forward excess return vs SPY,
emitting here gives the radar a backtest / hit-rate validation for free
(Plan §10, §16).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Phases that imply a constructive (long) directional read vs SPY, used so the
# grader's excess-return correctness check has a direction to test.
_BULLISH_PHASES = {"DISCOVERY_SEEDING", "EARLY_ACCUMULATION", "ACCELERATION", "MAINSTREAM_MOMENTUM"}
_BEARISH_PHASES = {"DISTRIBUTION_RISK", "EXIT_ROTATION_AWAY"}


def _rag_enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_RAG_ENABLE", "1").strip() != "0"


def _verdict_for_phase(phase: str) -> str:
    if phase in _BULLISH_PHASES:
        return "BUY"
    if phase in _BEARISH_PHASES:
        return "SELL"
    return "HOLD"


def emit_decisions(rows: List[Dict[str, Any]], snapshot_id: str) -> int:
    """Emit one theme-phase verdict per scored theme to the ledger (never raises)."""
    if not rows:
        return 0
    emitted = 0
    try:
        from .. import decision_ledger as dl
        from ..decision_ledger_registry import registry_attribution

        prompt_versions, snap_id, model = registry_attribution()

        for row in rows:
            theme_id = str(row.get("theme_id") or "")
            phase = str(row.get("lifecycle_phase") or "")
            scores = row.get("scores") or {}

            evidence = []
            if _rag_enabled():
                try:
                    from ..knowledge_store import get_knowledge_store

                    label = row.get("theme_label") or theme_id
                    _, refs = get_knowledge_store().query_with_refs(
                        "sp500_sector_analysis",
                        f"{label} sector rotation capital flow narrative {phase}",
                        n_results=2,
                    )
                    evidence = [
                        dl.EvidenceRef(
                            chunk_id=ref.get("chunk_id", ""),
                            collection=ref.get("collection", ""),
                            relevance=(
                                round(1.0 - float(ref["distance"]), 4)
                                if ref.get("distance") is not None else None
                            ),
                            rank=int(ref.get("rank", 0)),
                        )
                        for ref in refs
                        if ref.get("chunk_id")
                    ]
                except Exception:
                    evidence = []

            features = [
                dl.FeatureValue(name=name, value_num=value)
                for name, value in scores.items()
                if value is not None
            ]
            features.append(dl.FeatureValue(name="lifecycle_phase", value_str=phase))
            features.append(dl.FeatureValue(name="confidence", value_num=row.get("confidence_score")))

            dl.emit_decision(
                decision_type="theme_phase",
                symbol=theme_id,
                horizon_hint="21d",
                verdict=_verdict_for_phase(phase),
                confidence=(row.get("confidence_score") or 0) / 100.0,
                output={
                    "snapshot_id": snapshot_id,
                    "theme_id": theme_id,
                    "theme_label": row.get("theme_label"),
                    "lifecycle_phase": phase,
                    "recommendation_label": row.get("recommendation_label"),
                    "scores": scores,
                    "summary": (row.get("explanation") or {}).get("summary"),
                },
                source_route="backend/narrative_radar/engine.py::run_scan",
                evidence=evidence,
                features=features,
                prompt_versions=prompt_versions,
                registry_snapshot_id=snap_id,
                model=model,
            )
            emitted += 1
    except Exception as e:
        logger.warning("[NarrativeRadar] ledger emit failed (non-fatal): %s", e)
    return emitted
