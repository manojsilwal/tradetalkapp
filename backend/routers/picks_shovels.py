"""
Picks & Shovels Momentum Finder API (Plan §12).

GET  /picks-shovels/themes            → theme taxonomy for the heatmap/filters
POST /picks-shovels/refresh           → 202 Accepted, async scan (job pattern)
GET  /picks-shovels/status            → poll target for the frontend progress UI
GET  /picks-shovels/stocks            → ranked stocks from the latest snapshot (+ filters)
GET  /picks-shovels/stocks/{ticker}   → full detail for one ticker
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..picks_shovels import engine as ps_engine
from ..picks_shovels import store as ps_store
from ..picks_shovels import themes as ps_themes
from ..rate_limiter import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/picks-shovels", tags=["picks-shovels"])

_rl = rate_limit("default")
_rl_expensive = rate_limit("expensive")


@router.get("/themes", dependencies=[Depends(_rl)])
async def get_themes() -> Dict[str, Any]:
    return {
        "themes": ps_themes.THEMES,
        "universe_size": len(ps_themes.SEED_UNIVERSE),
    }


@router.post("/refresh", dependencies=[Depends(_rl_expensive)])
async def refresh(
    force: bool = Query(False, description="Bypass the 1-hour snapshot cache and rescan now"),
) -> JSONResponse:
    status = ps_engine.get_job_status()
    if status.get("status") == "running":
        return JSONResponse(
            status_code=200,
            content={"accepted": False, "reason": "already_running", "job": status},
        )
    if not force:
        cached = ps_store.fresh_snapshot_meta()
        if cached:
            return JSONResponse(
                status_code=200,
                content={
                    "accepted": False,
                    "cache_hit": True,
                    "reason": "fresh_snapshot",
                    "snapshot": cached,
                },
            )
    job = ps_engine.start_scan_task(force=force)
    return JSONResponse(status_code=202, content={"accepted": True, "cache_hit": False, "job": job})


@router.get("/status", dependencies=[Depends(_rl)])
async def get_status() -> Dict[str, Any]:
    return ps_engine.get_job_status()


def _passes_filters(
    row: Dict[str, Any],
    *,
    theme: Optional[str],
    min_score: Optional[float],
    hiddenness: Optional[str],
    market_cap_min: Optional[float],
    market_cap_max: Optional[float],
    min_revenue_growth: Optional[float],
    min_price_momentum: Optional[float],
    confidence: Optional[str],
) -> bool:
    if theme and theme not in (row.get("themes") or []):
        return False
    if min_score is not None and (row.get("final_score") or 0) < min_score:
        return False
    if hiddenness and (row.get("hiddenness_level") or "") != hiddenness:
        return False
    fund = row.get("fundamentals") or {}
    mcap = fund.get("market_cap")
    if market_cap_min is not None and (mcap is None or mcap < market_cap_min):
        return False
    if market_cap_max is not None and (mcap is None or mcap > market_cap_max):
        return False
    if min_revenue_growth is not None:
        rg = fund.get("revenue_growth_pct")
        if rg is None or rg < min_revenue_growth:
            return False
    if min_price_momentum is not None:
        pm = (row.get("score_breakdown") or {}).get("price_momentum_score")
        if pm is None or pm < min_price_momentum:
            return False
    if confidence and (row.get("confidence_level") or "") != confidence:
        return False
    return True


@router.get("/stocks", dependencies=[Depends(_rl)])
async def get_stocks(
    theme: Optional[str] = Query(None, description="theme_id filter"),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    hiddenness: Optional[str] = Query(None, description="Big Player | Secondary Player | Hidden Player"),
    market_cap_min: Optional[float] = Query(None, ge=0),
    market_cap_max: Optional[float] = Query(None, ge=0),
    min_revenue_growth: Optional[float] = Query(None),
    min_price_momentum: Optional[float] = Query(None, ge=0, le=100),
    confidence: Optional[str] = Query(None, description="High | Medium | Low"),
    sort: str = Query("final_score", description="final_score | hiddenness_score"),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    meta = ps_store.latest_snapshot_meta()
    if not meta:
        return {
            "snapshot": None,
            "items": [],
            "message": "No picks-and-shovels snapshot yet. Trigger a refresh first.",
        }
    rows = ps_store.load_snapshot_rows(meta["snapshot_id"], limit=500)
    filtered = [
        r for r in rows
        if _passes_filters(
            r,
            theme=theme,
            min_score=min_score,
            hiddenness=hiddenness,
            market_cap_min=market_cap_min,
            market_cap_max=market_cap_max,
            min_revenue_growth=min_revenue_growth,
            min_price_momentum=min_price_momentum,
            confidence=confidence,
        )
    ]
    sort_key = "hiddenness_score" if sort == "hiddenness_score" else "final_score"
    filtered.sort(key=lambda r: r.get(sort_key) or 0, reverse=True)

    items: List[Dict[str, Any]] = []
    for r in filtered[:limit]:
        fund = r.get("fundamentals") or {}
        items.append({
            "ticker": r.get("ticker"),
            "company_name": r.get("company_name"),
            "themes": r.get("themes"),
            "theme_labels": r.get("theme_labels"),
            "final_score": r.get("final_score"),
            "hiddenness_level": r.get("hiddenness_level"),
            "confidence_level": r.get("confidence_level"),
            "score_breakdown": r.get("score_breakdown"),
            "sector": r.get("sector"),
            "market_cap": fund.get("market_cap"),
            "revenue_growth_pct": fund.get("revenue_growth_pct"),
            "ret_3m_pct": (r.get("momentum") or {}).get("ret_3m_pct"),
            "why_selected": r.get("why_selected"),
            "risks": r.get("risks"),
        })

    age_s = max(0, int(time.time() - meta["created_at"]))
    # KPI summary for the dashboard header (Plan §11.2)
    theme_counts: Dict[str, int] = {}
    for r in rows:
        for t in (r.get("themes") or []):
            theme_counts[t] = theme_counts.get(t, 0) + 1
    top_theme = max(theme_counts.items(), key=lambda kv: kv[1])[0] if theme_counts else None
    scores = [r.get("final_score") for r in rows if r.get("final_score") is not None]
    summary = {
        "total_scanned": meta["scored"],
        "high_confidence": sum(1 for r in rows if r.get("confidence_level") == "High"),
        "hidden_players": sum(1 for r in rows if r.get("hiddenness_level") == "Hidden Player"),
        "top_theme": top_theme,
        "top_theme_label": ps_themes.theme_label(top_theme) if top_theme else None,
        "avg_final_score": round(sum(scores) / len(scores), 2) if scores else None,
    }
    return {
        "snapshot": meta,
        "age_seconds": age_s,
        "is_fresh": age_s <= ps_store.cache_ttl_s(),
        "summary": summary,
        "items": items,
    }


@router.get("/stocks/{ticker}", dependencies=[Depends(_rl)])
async def get_stock_detail(ticker: str) -> Dict[str, Any]:
    meta = ps_store.latest_snapshot_meta()
    if not meta:
        return {"ticker": ticker.upper(), "found": False, "message": "No snapshot yet."}
    row = ps_store.load_row(meta["snapshot_id"], ticker)
    if not row:
        return {"ticker": ticker.upper(), "found": False, "message": "Ticker not in latest snapshot."}
    return {"found": True, "snapshot": meta, **row}
