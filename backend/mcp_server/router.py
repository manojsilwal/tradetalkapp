"""
FastAPI router for S&P 500 Market Data MCP tools.

Mounted at /mcp/sp500/ on the main app.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from . import tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp/sp500", tags=["MCP S&P500"])


@router.get("/price-window")
async def price_window(
    symbol: str = Query(..., description="Ticker symbol"),
    start: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end: str = Query(..., description="End date (YYYY-MM-DD)"),
):
    """Retrieve OHLCV + technicals for a symbol within a date range."""
    rows = tools.get_price_window(symbol, start, end)
    return {"symbol": symbol, "start": start, "end": end, "count": len(rows), "data": rows}


@router.get("/movement-context")
async def movement_context(
    symbol: str = Query(..., description="Ticker symbol"),
    trade_date: str = Query(..., description="Date (YYYY-MM-DD)"),
):
    """Full context for a symbol on a date: features + attributed events."""
    result = tools.get_movement_context(symbol, trade_date)
    return result


@router.get("/causal-events")
async def causal_events(
    category: str = Query(..., description="Event category"),
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=500),
):
    """Events by category within a date range."""
    rows = tools.get_causal_events(category, start_date, end_date, limit)
    return {"category": category, "count": len(rows), "events": rows}


@router.get("/similar-events")
async def similar_events(
    query_text: str = Query(..., description="Natural language query"),
    top_k: int = Query(10, ge=1, le=50),
    category_filter: Optional[str] = Query(None, description="Optional category filter"),
):
    """Semantic search for historically similar events."""
    results = tools.find_similar_events(query_text, top_k, category_filter)
    return {"query": query_text, "count": len(results), "results": results}


@router.get("/gold-spx-context")
async def gold_spx_context(
    trade_date: str = Query(..., description="Date (YYYY-MM-DD)"),
):
    """Gold-equity correlation, risk regime, and DXY context."""
    result = tools.get_gold_spx_context(trade_date)
    return result


@router.get("/live-quote")
async def live_quote(
    symbol: str = Query(..., description="S&P 500 ticker symbol"),
):
    """Live spot quote with hedged multi-provider fetch and data-lake EOD fallback."""
    from ..data_errors import InsufficientDataError

    try:
        result = await tools.get_live_quote(symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if result.get("price") is None:
        sym = (symbol or "").upper().strip()
        raise InsufficientDataError(
            "live_quote",
            f"No live or data-lake quote available for {sym}.",
            ticker=sym,
            missing=["spot_price"],
        )
    return result


@router.get("/live-quotes")
async def live_quotes(
    symbols: str = Query(..., description="Comma-separated S&P 500 tickers"),
):
    """Bulk live spot quotes (parallel Yahoo batch + per-symbol hedged fallback)."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {"count": 0, "quotes": []}
    rows = await tools.get_live_quotes(sym_list)
    return {"count": len(rows), "quotes": rows}


@router.get("/tools")
async def list_tools():
    """List available MCP tools and their parameters."""
    return {"tools": tools.TOOL_DESCRIPTORS}
