"""
Evidence timeline for a theme (Plan §10.3, §11.4, §21 MVP requirement).

Assembles a chronological, explainable trail for a theme from three sources:

  1. Lifecycle-phase history — the ``decision_type="theme_phase"`` rows the radar
     emits to the Decision-Outcome Ledger over time. Phase *transitions* become
     timeline events (this is how an investor sees "AI Infra → Distribution Risk").
  2. Current-snapshot alerts (saturation / distribution / exit warnings, etc.).
  3. Best-effort RAG evidence chunks (sector-analysis narratives) when available.

The phase-history builder is pure (``phase_timeline_from_history``) so it is fully
offline-testable; the live wrappers add the ledger / store / RAG reads and never
raise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import lifecycle as nr_lifecycle

logger = logging.getLogger(__name__)


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def phase_timeline_from_history(history: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pure: turn a theme's phase history into transition events (newest first).

    ``history`` items: ``{created_at, lifecycle_phase, verdict, confidence}`` in any
    order. Only phase *changes* (and the first observation) emit an event.
    """
    rows = sorted(history, key=lambda h: h.get("created_at") or 0.0)
    events: List[Dict[str, Any]] = []
    prev: Optional[str] = None
    for h in rows:
        phase = h.get("lifecycle_phase") or ""
        if not phase or phase == prev:
            continue
        conf = h.get("confidence")
        conf_pct = round(float(conf) * 100.0) if conf is not None else None
        events.append({
            "date": _iso(h.get("created_at")),
            "event_type": "PHASE_TRANSITION" if prev else "PHASE_SET",
            "source_type": "SYSTEM",
            "title": f"Phase → {nr_lifecycle.phase_label(phase)}",
            "summary": (
                f"Lifecycle phase {'changed to' if prev else 'set to'} "
                f"{nr_lifecycle.phase_label(phase)}"
                + (f" (confidence {conf_pct})." if conf_pct is not None else ".")
            ),
            "phase": phase,
        })
        prev = phase
    events.reverse()  # newest first
    return events


def fetch_phase_history(theme_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Read theme-phase decisions for this theme from the ledger (best-effort)."""
    try:
        from .. import decision_ledger as dl

        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="theme_phase", limit=limit)
        target = theme_id.upper()
        out: List[Dict[str, Any]] = []
        for d in decisions:
            if (getattr(d, "symbol", "") or "").upper() != target:
                continue
            phase = (getattr(d, "output", {}) or {}).get("lifecycle_phase") or ""
            out.append({
                "created_at": getattr(d, "created_at", 0.0),
                "lifecycle_phase": phase,
                "verdict": getattr(d, "verdict", ""),
                "confidence": getattr(d, "confidence", None),
            })
        return out
    except Exception as e:
        logger.debug("[NarrativeRadar] phase history read failed for %s: %s", theme_id, e)
        return []


def _alert_events(alerts: Sequence[Dict[str, Any]], when: Optional[float]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in alerts or []:
        out.append({
            "date": _iso(when),
            "event_type": a.get("alert_type"),
            "source_type": "SYSTEM",
            "title": a.get("title"),
            "summary": a.get("explanation"),
            "severity": a.get("severity"),
        })
    return out


def _rag_events(theme_id: str, theme_label: str, limit: int = 3) -> List[Dict[str, Any]]:
    try:
        from ..knowledge_store import get_knowledge_store

        docs, refs = get_knowledge_store().query_with_refs(
            "sp500_sector_analysis",
            f"{theme_label} sector rotation capital flow narrative",
            n_results=limit,
        )
        out: List[Dict[str, Any]] = []
        for doc, ref in zip(docs or [], refs or []):
            out.append({
                "date": None,
                "event_type": "NARRATIVE_EVIDENCE",
                "source_type": "NEWS",
                "title": "Supporting sector-analysis evidence",
                "summary": (doc or "")[:280],
                "chunk_id": ref.get("chunk_id"),
                "collection": ref.get("collection"),
            })
        return out
    except Exception:
        return []


def build_timeline(
    theme_id: str,
    theme_label: str = "",
    *,
    alerts: Optional[Sequence[Dict[str, Any]]] = None,
    alerts_when: Optional[float] = None,
    include_rag: bool = True,
) -> List[Dict[str, Any]]:
    """Assemble the full evidence timeline (newest first). Never raises."""
    events: List[Dict[str, Any]] = []
    events.extend(phase_timeline_from_history(fetch_phase_history(theme_id)))
    events.extend(_alert_events(alerts or [], alerts_when))
    if include_rag:
        events.extend(_rag_events(theme_id, theme_label or theme_id))

    # Sort: dated events newest-first, undated (RAG) appended at the end.
    dated = [e for e in events if e.get("date")]
    undated = [e for e in events if not e.get("date")]
    dated.sort(key=lambda e: e.get("date") or "", reverse=True)
    return dated + undated
