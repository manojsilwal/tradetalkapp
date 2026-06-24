"""Long-horizon investment surface endpoints.

A read-only re-framing of the finance brain for a 1-5 year investment horizon
(minimum 12 months). Reuses the brain snapshot + Reflex live re-inference and
the :mod:`backend.brain.investment_stance` layer. Gated by ``INVESTMENT_SURFACE``
(on top of ``BRAIN_SERVE_ENABLE``) so it is OFF by default and the existing
quarterly surfaces are unaffected.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/investment", tags=["investment"])


def _knowledge_store():
    try:
        from ..deps import knowledge_store
        return knowledge_store
    except Exception:  # noqa: BLE001
        return None


@router.get("/health")
def investment_health() -> Dict[str, Any]:
    from ..brain.serving import investment_surface_enabled, serving_enabled
    from ..brain.investment_stance import investment_horizon_meta
    out: Dict[str, Any] = {
        "investment_surface_enabled": investment_surface_enabled(),
        "brain_serving_enabled": serving_enabled(),
        "horizon": investment_horizon_meta(),
    }
    try:
        from ..brain.run_brain_pipeline import read_status
        out["last_run"] = read_status()
    except Exception:  # noqa: BLE001
        out["last_run"] = None
    return out


@router.get("/analyze-company")
def analyze_company(ticker: str = Query(..., min_length=1, max_length=12),
                    as_of_date: Optional[str] = None) -> Dict[str, Any]:
    from ..brain.serving import serve_investment_analysis, investment_surface_enabled
    if not investment_surface_enabled():
        raise HTTPException(status_code=503,
                            detail="investment surface disabled (set BRAIN_SERVE_ENABLE=1 and INVESTMENT_SURFACE=1)")
    result = serve_investment_analysis(ticker, as_of_date=as_of_date,
                                       knowledge_store=_knowledge_store())
    if result.get("status") == "no_snapshot":
        raise HTTPException(status_code=404, detail=result.get("reason", "no snapshot"))
    return result


@router.get("/valuation-freshness")
def valuation_freshness(ticker: str = Query(..., min_length=1, max_length=12),
                        as_of_date: Optional[str] = None) -> Dict[str, Any]:
    from ..brain.serving import serve_investment_analysis, investment_surface_enabled
    if not investment_surface_enabled():
        raise HTTPException(status_code=503,
                            detail="investment surface disabled (set BRAIN_SERVE_ENABLE=1 and INVESTMENT_SURFACE=1)")
    result = serve_investment_analysis(ticker, as_of_date=as_of_date,
                                       knowledge_store=_knowledge_store())
    if result.get("status") == "no_snapshot":
        raise HTTPException(status_code=404, detail=result.get("reason", "no snapshot"))
    return {
        "ticker": result.get("ticker"),
        "as_of_date": result.get("as_of_date"),
        "valuation_freshness": result.get("valuation_freshness"),
        "pricing_context": result.get("pricing_context"),
        "minimum_horizon_months": result.get("minimum_horizon_months"),
    }
