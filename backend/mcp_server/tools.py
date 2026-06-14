"""
S&P 500 Market Data MCP tools — 5 query tools for the finance swarm.

Each tool queries the backend (DuckDB or BigQuery) and returns structured data
that agents can consume directly.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .backend import backend
from .bq_schema import FULL_DATASET

logger = logging.getLogger(__name__)


def get_price_window(
    symbol: str,
    start: str,
    end: str,
) -> List[Dict[str, Any]]:
    """
    Retrieve OHLCV + derived technicals for a symbol within a date range.

    Args:
        symbol: Ticker symbol (e.g. "AAPL")
        start: Start date ISO format (e.g. "2024-01-01")
        end: End date ISO format (e.g. "2024-12-31")

    Returns:
        List of daily price rows with OHLCV, daily_return_pct, MAs, relative_volume.
    """
    symbol = symbol.upper().strip()
    sql = f"""
        SELECT symbol, trade_date, open, high, low, close, volume,
               daily_return_pct, ma_20, ma_50, ma_200, relative_volume
        FROM daily_prices
        WHERE symbol = '{symbol}'
          AND trade_date >= '{start}'
          AND trade_date <= '{end}'
        ORDER BY trade_date
    """
    rows = backend().query(sql)
    for row in rows:
        if "trade_date" in row and hasattr(row["trade_date"], "isoformat"):
            row["trade_date"] = row["trade_date"].isoformat()
    return rows


def get_movement_context(
    symbol: str,
    trade_date: str,
) -> Dict[str, Any]:
    """
    Get full context for a symbol on a specific date: price + linked events + catalyst.

    Reads from movement_context_daily when available; falls back to legacy joins.
    """
    symbol = symbol.upper().strip()

    ctx_sql = f"""
        SELECT *
        FROM movement_context_daily
        WHERE symbol = '{symbol}' AND trade_date = '{trade_date}'
        LIMIT 1
    """
    ctx_rows = backend().query(ctx_sql)

    if ctx_rows:
        row = ctx_rows[0]
        if "trade_date" in row and hasattr(row["trade_date"], "isoformat"):
            row["trade_date"] = row["trade_date"].isoformat()

        gold = get_gold_spx_context(trade_date)

        return {
            "symbol": symbol,
            "trade_date": trade_date,
            "price": {
                "close": row.get("close"),
                "volume": row.get("volume"),
                "daily_return_pct": row.get("daily_return_pct"),
                "relative_volume": row.get("relative_volume"),
            },
            "features": {
                "return_zscore_60d": row.get("return_zscore_60d"),
                "market_regime": row.get("market_regime"),
            },
            "same_day_events": row.get("same_day_events_json") or [],
            "lagged_events": row.get("lagged_events_json") or [],
            "macro_events": row.get("macro_events_json") or [],
            "linked_events": row.get("linked_events_json") or [],
            "catalyst_status": row.get("catalyst_status", "no_catalyst"),
            "primary_cause": {
                "category": row.get("primary_cause_category"),
                "headline": row.get("primary_cause_headline"),
                "attribution_weight": row.get("primary_cause_weight"),
            } if row.get("primary_cause_category") else None,
            "macro_context": {
                "spx_return": row.get("spx_return"),
                "risk_regime": row.get("risk_regime"),
            },
            "gold_context": gold,
            # backward compat
            "attributed_events": row.get("linked_events_json") or [],
        }

    # Legacy fallback
    features_sql = f"""
        SELECT *
        FROM daily_movement_features
        WHERE symbol = '{symbol}' AND trade_date = '{trade_date}'
        LIMIT 1
    """
    features = backend().query(features_sql)

    attr_sql = f"""
        SELECT a.event_id, a.attribution_weight, a.lag_days, a.category,
               e.headline, e.sentiment_score
        FROM price_movement_attributions a
        LEFT JOIN events_curated e ON a.event_id = e.event_id
        WHERE a.symbol = '{symbol}' AND a.move_date = '{trade_date}'
        ORDER BY a.attribution_weight DESC
        LIMIT 10
    """
    try:
        attributions = backend().query(attr_sql)
    except Exception:
        attributions = []

    price_sql = f"""
        SELECT symbol, trade_date, close, daily_return_pct, relative_volume
        FROM daily_prices
        WHERE symbol = '{symbol}' AND trade_date = '{trade_date}'
        LIMIT 1
    """
    price_row = backend().query(price_sql)

    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "price": price_row[0] if price_row else None,
        "features": features[0] if features else None,
        "attributed_events": attributions,
        "catalyst_status": "no_catalyst" if not attributions else "symbol_specific",
    }


def get_causal_events(
    category: str,
    start_date: str,
    end_date: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Retrieve curated events by category within a date range.

    Args:
        category: Event category (earnings, fed_decision, macro_data, geopolitical, tariff_policy, insider_trade)
        start_date: Start date ISO format
        end_date: End date ISO format
        limit: Max rows to return

    Returns:
        List of event dicts with headline, sentiment, affected symbols.
    """
    category = category.lower().strip()
    sql = f"""
        SELECT event_id, published_at, category, source, headline,
               sentiment_score, affected_symbols
        FROM events_curated
        WHERE category = '{category}'
          AND published_at >= '{start_date}'
          AND published_at <= '{end_date}'
        ORDER BY published_at DESC
        LIMIT {limit}
    """
    rows = backend().query(sql)
    for row in rows:
        if "published_at" in row and hasattr(row["published_at"], "isoformat"):
            row["published_at"] = row["published_at"].isoformat()
    return rows


def find_similar_events(
    query_text: str,
    top_k: int = 10,
    category_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Semantic search over events using Knowledge Store embeddings,
    then hydrate full context from BigQuery.

    Args:
        query_text: Natural language query (e.g. "tariff announcement affecting semiconductors")
        top_k: Number of results
        category_filter: Optional category to narrow search

    Returns:
        List of event dicts with similarity scores.
    """
    try:
        from ..knowledge_store import get_knowledge_store
        results = get_knowledge_store().query_with_refs(
            collection="events_semantic",
            query_text=query_text,
            n_results=top_k,
            metadata_filter={"category": category_filter} if category_filter else None,
        )
    except Exception as e:
        logger.warning("[find_similar_events] Knowledge store query failed: %s", e)
        return []

    if not results:
        return []

    event_ids = [r.get("event_id") or r.get("id", "") for r in results if r]
    if not event_ids:
        return results

    ids_str = ",".join(f"'{eid}'" for eid in event_ids if eid)
    if not ids_str:
        return results

    hydrate_sql = f"""
        SELECT event_id, published_at, category, headline, sentiment_score, affected_symbols
        FROM events_curated
        WHERE event_id IN ({ids_str})
    """
    try:
        hydrated = backend().query(hydrate_sql)
        hydrated_map = {r["event_id"]: r for r in hydrated}
        for r in results:
            eid = r.get("event_id") or r.get("id", "")
            if eid in hydrated_map:
                r.update(hydrated_map[eid])
    except Exception:
        pass

    return results


def get_gold_spx_context(
    trade_date: str,
) -> Dict[str, Any]:
    """
    Get gold-equity correlation context for a specific date.

    Args:
        trade_date: Date ISO format

    Returns:
        Dict with SPX return, XAU return, correlation, risk regime, DXY, real yield.
    """
    sql = f"""
        SELECT trade_date, spx_return, xau_return, dxy_return,
               spx_xau_corr_30d, risk_regime, real_yield_10y
        FROM gold_correlation_daily
        WHERE trade_date = '{trade_date}'
        LIMIT 1
    """
    rows = backend().query(sql)
    if not rows:
        return {"trade_date": trade_date, "available": False}

    row = rows[0]
    if "trade_date" in row and hasattr(row["trade_date"], "isoformat"):
        row["trade_date"] = row["trade_date"].isoformat()
    row["available"] = True
    return row


def _latest_close_from_lake(symbol: str) -> Optional[Dict[str, Any]]:
    """Last EOD close from sp500-ingest data lake (daily_prices)."""
    from ..connectors.live_quote import latest_close_from_lake

    return latest_close_from_lake(symbol)


async def get_live_quote(symbol: str) -> Dict[str, Any]:
    """Live (or data-lake EOD fallback) quote for one S&P 500 symbol."""
    from ..connectors.live_quote import get_live_quote as _get

    payload, _ = await _get(symbol)
    return payload


async def get_live_quotes(symbols: List[str]) -> List[Dict[str, Any]]:
    """Bulk live quotes for S&P 500 symbols."""
    from ..connectors.live_quote import get_live_quotes as _get_many

    return await _get_many(symbols)


TOOL_DESCRIPTORS = [
    {
        "name": "get_price_window",
        "description": "Retrieve OHLCV + technicals for a symbol within a date range",
        "parameters": {
            "symbol": {"type": "string", "required": True},
            "start": {"type": "string", "format": "date", "required": True},
            "end": {"type": "string", "format": "date", "required": True},
        },
    },
    {
        "name": "get_movement_context",
        "description": "Full context for a symbol on a date: price movement + linked news/events + catalyst",
        "parameters": {
            "symbol": {"type": "string", "required": True},
            "trade_date": {"type": "string", "format": "date", "required": True},
        },
    },
    {
        "name": "get_causal_events",
        "description": "Events by category within a date range (earnings, fed, macro, geopolitical, tariff, insider)",
        "parameters": {
            "category": {"type": "string", "required": True},
            "start_date": {"type": "string", "format": "date", "required": True},
            "end_date": {"type": "string", "format": "date", "required": True},
            "limit": {"type": "integer", "default": 50},
        },
    },
    {
        "name": "find_similar_events",
        "description": "Semantic search for historically similar events",
        "parameters": {
            "query_text": {"type": "string", "required": True},
            "top_k": {"type": "integer", "default": 10},
            "category_filter": {"type": "string", "required": False},
        },
    },
    {
        "name": "get_gold_spx_context",
        "description": "Gold-equity correlation, risk regime, and DXY context for a date",
        "parameters": {
            "trade_date": {"type": "string", "format": "date", "required": True},
        },
    },
    {
        "name": "get_live_quote",
        "description": "Live spot quote for an S&P 500 symbol (hedged multi-provider; data-lake EOD fallback)",
        "parameters": {
            "symbol": {"type": "string", "required": True},
        },
    },
    {
        "name": "get_live_quotes",
        "description": "Bulk live spot quotes for S&P 500 symbols",
        "parameters": {
            "symbols": {"type": "array", "items": {"type": "string"}, "required": True},
        },
    },
]
