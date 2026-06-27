"""
Narrative Rotation Radar API (Plan §10, NR-4).

GET  /narrative-radar/themes              → theme taxonomy (+ keyword dictionaries)
POST /narrative-radar/refresh             → 202 Accepted, async scan (job pattern)
GET  /narrative-radar/status              → poll target for the frontend progress UI
GET  /narrative-radar/overview            → radar: every theme's phase + scores (latest snapshot)
GET  /narrative-radar/themes/{slug}       → full detail for one theme
GET  /narrative-radar/themes/{slug}/stocks→ theme members (+ Picks & Shovels rows when available)

Responses are plain dicts (same convention as the Picks & Shovels router). All
scores carry confidence + the compliance disclaimer; deferred signal families are
reported as pending, never fabricated.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..narrative_radar import engine as nr_engine
from ..narrative_radar import explain as nr_explain
from ..narrative_radar import store as nr_store
from ..narrative_radar import themes as nr_themes
from ..rate_limiter import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/narrative-radar", tags=["narrative-radar"])

_rl = rate_limit("default")
_rl_expensive = rate_limit("expensive")


@router.get("/themes", dependencies=[Depends(_rl)])
async def get_themes() -> Dict[str, Any]:
    return {
        "themes": nr_themes.THEMES,
        "theme_count": len(nr_themes.theme_ids()),
        "universe_size": len(nr_themes.theme_universe()),
        "disclaimer": nr_explain.DISCLAIMER,
    }


@router.post("/refresh", dependencies=[Depends(_rl_expensive)])
async def refresh(
    force: bool = Query(False, description="Bypass the 1-hour snapshot cache and rescan now"),
) -> JSONResponse:
    status = nr_engine.get_job_status()
    if status.get("status") == "running":
        return JSONResponse(status_code=200, content={"accepted": False, "reason": "already_running", "job": status})
    if not force:
        cached = nr_store.fresh_snapshot_meta()
        if cached:
            return JSONResponse(
                status_code=200,
                content={"accepted": False, "cache_hit": True, "reason": "fresh_snapshot", "snapshot": cached},
            )
    job = nr_engine.start_scan_task(force=force)
    return JSONResponse(status_code=202, content={"accepted": True, "cache_hit": False, "job": job})


@router.get("/status", dependencies=[Depends(_rl)])
async def get_status() -> Dict[str, Any]:
    return nr_engine.get_job_status()


def _overview_item(row: Dict[str, Any]) -> Dict[str, Any]:
    s = row.get("scores") or {}
    return {
        "theme_id": row.get("theme_id"),
        "theme_label": row.get("theme_label"),
        "lifecycle_phase": row.get("lifecycle_phase"),
        "phase_label": row.get("phase_label"),
        "recommendation_label": row.get("recommendation_label"),
        "confidence_level": row.get("confidence_level"),
        "confidence_score": row.get("confidence_score"),
        "summary": row.get("summary"),
        "scores": {
            "market_confirmation_score": s.get("market_confirmation_score"),
            "breadth_quality_score": s.get("breadth_quality_score"),
            "institutional_conviction_score": s.get("institutional_conviction_score"),
            "productization_score": s.get("productization_score"),
            "narrative_score": s.get("narrative_score"),
            "retail_saturation_score": s.get("retail_saturation_score"),
            "narrative_reality_alignment_score": s.get("narrative_reality_alignment_score"),
            "macro_tailwind_score": s.get("macro_tailwind_score"),
            "theme_formation_score": s.get("theme_formation_score"),
            "theme_accumulation_score": s.get("theme_accumulation_score"),
            "theme_acceleration_score": s.get("theme_acceleration_score"),
            "theme_distribution_risk_score": s.get("theme_distribution_risk_score"),
            "theme_exit_risk_score": s.get("theme_exit_risk_score"),
        },
        "available_families": row.get("available_families") or [],
        "pending_signal_families": (row.get("explanation") or {}).get("pending_signal_families") or [],
    }


@router.get("/overview", dependencies=[Depends(_rl)])
async def get_overview(
    phase: Optional[str] = Query(None, description="lifecycle phase filter"),
    min_confidence: Optional[float] = Query(None, ge=0, le=100),
    sort: str = Query("acceleration", description="acceleration | exit_risk | formation | confidence"),
    limit: int = Query(50, ge=1, le=100),
) -> Dict[str, Any]:
    meta = nr_store.latest_snapshot_meta()
    if not meta:
        return {
            "snapshot": None,
            "themes": [],
            "disclaimer": nr_explain.DISCLAIMER,
            "message": "No narrative-radar snapshot yet. Trigger a refresh first.",
        }
    rows = nr_store.load_snapshot_rows(meta["snapshot_id"])

    def _passes(r: Dict[str, Any]) -> bool:
        if phase and (r.get("lifecycle_phase") or "") != phase:
            return False
        if min_confidence is not None and (r.get("confidence_score") or 0) < min_confidence:
            return False
        return True

    filtered = [r for r in rows if _passes(r)]
    sort_map = {
        "acceleration": ("theme_acceleration_score", True),
        "exit_risk": ("theme_exit_risk_score", True),
        "formation": ("theme_formation_score", True),
    }
    if sort == "confidence":
        filtered.sort(key=lambda r: r.get("confidence_score") or 0, reverse=True)
    else:
        key, desc = sort_map.get(sort, ("theme_acceleration_score", True))
        filtered.sort(key=lambda r: (r.get("scores") or {}).get(key) or 0, reverse=desc)

    age_s = max(0, int(time.time() - meta["created_at"]))
    phase_counts: Dict[str, int] = {}
    available_union: set = set()
    for r in rows:
        p = r.get("lifecycle_phase") or "UNKNOWN"
        phase_counts[p] = phase_counts.get(p, 0) + 1
        available_union.update(r.get("available_families") or [])

    return {
        "snapshot": meta,
        "age_seconds": age_s,
        "is_fresh": age_s <= nr_store.cache_ttl_s(),
        "phase_counts": phase_counts,
        "themes": [_overview_item(r) for r in filtered[:limit]],
        "data_freshness": nr_explain.data_freshness(sorted(available_union)),
        "disclaimer": nr_explain.DISCLAIMER,
    }


@router.get("/alerts", dependencies=[Depends(_rl)])
async def get_alerts(
    severity: Optional[str] = Query(None, description="info | medium | high"),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    meta = nr_store.latest_snapshot_meta()
    if not meta:
        return {"snapshot": None, "alerts": [], "disclaimer": nr_explain.DISCLAIMER}
    alerts = nr_store.load_alerts(meta["snapshot_id"], severity=severity, limit=limit)
    return {"snapshot": meta, "alerts": alerts, "disclaimer": nr_explain.DISCLAIMER}


@router.get("/backtests", dependencies=[Depends(_rl)])
async def get_backtests(horizon: str = Query("21d", description="1d | 5d | 21d | 63d | 252d")) -> Dict[str, Any]:
    from ..narrative_radar import backtests as nr_backtests

    summary = nr_backtests.overall_summary(horizon=horizon)
    summary["disclaimer"] = nr_explain.DISCLAIMER
    summary["note"] = (
        "Hit rate = share of theme-phase calls with correct directional excess return vs SPY, "
        "graded by the Decision-Outcome Ledger. Accumulates over time as decisions mature."
    )
    return summary


@router.get("/themes/{slug}", dependencies=[Depends(_rl)])
async def get_theme_detail(slug: str) -> Dict[str, Any]:
    meta = nr_store.latest_snapshot_meta()
    if not meta:
        return {"theme_id": slug, "found": False, "message": "No snapshot yet."}
    row = nr_store.load_row(meta["snapshot_id"], slug)
    if not row:
        return {"theme_id": slug, "found": False, "message": "Theme not in latest snapshot."}
    backtest = None
    try:
        from ..narrative_radar import backtests as nr_backtests

        matches = [r for r in nr_backtests.theme_phase_hit_rates(horizon="21d", limit=500)
                   if (r.get("theme_id") or "").upper() == slug.upper()]
        backtest = matches[0] if matches else None
    except Exception:
        backtest = None
    return {
        "found": True, "snapshot": meta, "members": nr_themes.theme_members(slug),
        "backtest": backtest, **row,
    }


@router.get("/themes/{slug}/timeline", dependencies=[Depends(_rl)])
async def get_theme_timeline(slug: str) -> Dict[str, Any]:
    """Chronological evidence trail: lifecycle-phase transitions (from the ledger),
    current alerts, and supporting RAG evidence (Plan §10.3, §11.4)."""
    from ..narrative_radar import timeline as nr_timeline

    meta = nr_store.latest_snapshot_meta()
    alerts: List[Dict[str, Any]] = []
    alerts_when = None
    label = nr_themes.theme_label(slug)
    if meta:
        try:
            all_alerts = nr_store.load_alerts(meta["snapshot_id"], limit=200)
            alerts = [a for a in all_alerts if (a.get("theme_id") or "") == slug]
            alerts_when = meta.get("created_at")
        except Exception:
            alerts = []
    events = nr_timeline.build_timeline(slug, label, alerts=alerts, alerts_when=alerts_when)
    return {
        "theme_id": slug,
        "theme_label": label,
        "events": events,
        "disclaimer": nr_explain.DISCLAIMER,
    }


@router.get("/themes/{slug}/stocks", dependencies=[Depends(_rl)])
async def get_theme_stocks(slug: str, limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """Theme member beneficiaries; enriched with Picks & Shovels rows when a snapshot exists."""
    members = nr_themes.theme_members(slug)
    items: List[Dict[str, Any]] = [{"ticker": tk} for tk in members]
    try:
        from ..picks_shovels import store as ps_store

        ps_meta = ps_store.latest_snapshot_meta()
        if ps_meta:
            ps_rows = {r["ticker"]: r for r in ps_store.load_snapshot_rows(ps_meta["snapshot_id"], limit=500)}
            enriched: List[Dict[str, Any]] = []
            for tk in members:
                r = ps_rows.get(tk)
                if r:
                    enriched.append({
                        "ticker": tk,
                        "company_name": r.get("company_name"),
                        "final_score": r.get("final_score"),
                        "hiddenness_level": r.get("hiddenness_level"),
                        "confidence_level": r.get("confidence_level"),
                        "why_selected": r.get("why_selected"),
                    })
                else:
                    enriched.append({"ticker": tk})
            items = enriched
    except Exception:
        pass
    return {
        "theme_id": slug,
        "theme_label": nr_themes.theme_label(slug),
        "members": members,
        "items": items[:limit],
        "disclaimer": nr_explain.DISCLAIMER,
    }
