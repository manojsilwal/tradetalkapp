"""Finance-brain serving endpoints.

Read-only verdict serving from the brain (snapshot + Reflex live re-inference).
Gated by ``BRAIN_SERVE_ENABLE`` so the cutover can be toggled per environment.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/brain", tags=["brain"])


@router.get("/health")
def brain_health() -> Dict[str, Any]:
    from ..brain.serving import serving_enabled
    out: Dict[str, Any] = {"serving_enabled": serving_enabled()}
    try:
        from ..brain.run_brain_pipeline import read_status
        out["last_run"] = read_status()
    except Exception:  # noqa: BLE001
        out["last_run"] = None
    return out


@router.get("/ticker")
def brain_ticker(ticker: str = Query(..., min_length=1, max_length=12),
                 as_of_date: Optional[str] = None) -> Dict[str, Any]:
    from ..brain.serving import serve_ticker, serving_enabled
    if not serving_enabled():
        raise HTTPException(status_code=503, detail="brain serving disabled (set BRAIN_SERVE_ENABLE=1)")
    try:
        from ..deps import knowledge_store
    except Exception:  # noqa: BLE001
        knowledge_store = None
    result = serve_ticker(ticker, as_of_date=as_of_date, knowledge_store=knowledge_store)
    if result.get("status") == "no_snapshot":
        raise HTTPException(status_code=404, detail=result.get("reason", "no snapshot"))
    return result
