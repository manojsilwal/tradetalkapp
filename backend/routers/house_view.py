"""
House View router (Phase 4) — fused Super Investor verdict per ticker.

``GET /house-view?ticker=NVDA&horizon=21d`` returns the calibrated forecast
band, the recent multi-agent consensus, and a single fused verdict with a
position-size hint. Every response emits a ``house_view`` decision to the
ledger so the nightly grader builds a public track record for this surface.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from ..deps import knowledge_store
from ..house_view import build_house_view, house_view_enabled

router = APIRouter(tags=["house-view"])

_VALID_HORIZONS = ("1d", "5d", "21d", "63d")


@router.get("/house-view")
async def get_house_view(
    ticker: str = Query(..., min_length=1, max_length=10),
    horizon: str = Query(default="21d"),
):
    if not house_view_enabled():
        raise HTTPException(status_code=503, detail="house view disabled (HOUSE_VIEW_ENABLE=0)")
    h = horizon.lower().strip()
    if h not in _VALID_HORIZONS:
        raise HTTPException(
            status_code=400,
            detail=f"horizon must be one of {list(_VALID_HORIZONS)}",
        )
    emit = (os.getenv("DECISION_LEDGER_ENABLE", "1").strip().lower() or "1") in (
        "1", "true", "yes", "on",
    )
    return await build_house_view(
        ticker, horizon=h, knowledge_store=knowledge_store, emit_ledger=emit,
    )
