"""
Decision-Outcome Ledger emit for the Picks & Shovels Momentum Finder.

Per AGENTS.md (Harness Engineering Phase 2): every user-facing surface that produces
a verdict MUST emit to the ledger before returning. This mirrors
``actionable_companies._emit_ledger_decisions``: top-N ranked picks are emitted with
``decision_type="picks_shovels_momentum"``, the 7 component scores as features, and
best-effort RAG evidence. The whole thing is wrapped so a ledger failure can never
break the scan.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _top_n() -> int:
    return max(0, int(os.environ.get("PICKS_SHOVELS_LEDGER_TOP_N", "15") or "15"))


def _rag_enabled() -> bool:
    return os.environ.get("PICKS_SHOVELS_RAG_ENABLE", "1").strip() != "0"


def emit_decisions(rows: List[Dict[str, Any]], snapshot_id: str) -> int:
    """Emit the top picks-and-shovels verdicts to the ledger (never raises)."""
    top_n = _top_n()
    if top_n == 0 or not rows:
        return 0
    emitted = 0
    try:
        from .. import decision_ledger as dl
        from ..decision_ledger_registry import registry_attribution

        prompt_versions, snap_id, model = registry_attribution()
        ranked = sorted(
            (r for r in rows if r.get("final_score") is not None),
            key=lambda r: r.get("final_score") or 0,
            reverse=True,
        )[:top_n]

        for row in ranked:
            ticker = str(row.get("ticker") or "").upper()
            evidence = []
            if _rag_enabled():
                try:
                    from ..knowledge_store import get_knowledge_store

                    _, refs = get_knowledge_store().query_with_refs(
                        "sp500_fundamentals_narratives",
                        f"{ticker} picks and shovels momentum infrastructure supplier",
                        n_results=2,
                        where={"ticker": ticker},
                    )
                    evidence = [
                        dl.EvidenceRef(
                            chunk_id=ref.get("chunk_id", ""),
                            collection=ref.get("collection", ""),
                            relevance=(
                                round(1.0 - float(ref["distance"]), 4)
                                if ref.get("distance") is not None
                                else None
                            ),
                            rank=int(ref.get("rank", 0)),
                        )
                        for ref in refs
                        if ref.get("chunk_id")
                    ]
                except Exception:
                    evidence = []

            breakdown = row.get("score_breakdown") or {}
            features = [
                dl.FeatureValue(name=name, value_num=value)
                for name, value in breakdown.items()
                if value is not None
            ]
            primary_theme = (row.get("themes") or [""])[0]
            features.append(dl.FeatureValue(name="hiddenness", value_str=row.get("hiddenness_level") or ""))
            features.append(dl.FeatureValue(name="primary_theme", value_str=primary_theme))
            features.append(dl.FeatureValue(name="coverage", value_num=row.get("coverage")))

            dl.emit_decision(
                decision_type="picks_shovels_momentum",
                symbol=ticker,
                horizon_hint="21d",
                verdict=row.get("hiddenness_level") or "",
                confidence=(row.get("confidence_score") or 0) / 100.0,
                output={
                    "snapshot_id": snapshot_id,
                    "final_score": row.get("final_score"),
                    "themes": row.get("themes"),
                    "score_breakdown": breakdown,
                    "hiddenness_level": row.get("hiddenness_level"),
                    "confidence_level": row.get("confidence_level"),
                    "why_selected": (row.get("explanation") or {}).get("why_selected"),
                },
                source_route="backend/picks_shovels/engine.py::run_scan",
                evidence=evidence,
                features=features,
                prompt_versions=prompt_versions,
                registry_snapshot_id=snap_id,
                model=model,
            )
            emitted += 1
    except Exception as e:
        logger.warning("[PicksShovels] ledger emit failed (non-fatal): %s", e)
    return emitted
