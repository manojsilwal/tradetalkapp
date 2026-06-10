"""
Kalshi prediction market connector.

Uses the public Kalshi Trade API v2 (no auth required for market data).
Fetches events by keyword search, tagging results as "direct" (company-specific)
or "sector" (index/ETF-level bets relevant to the ticker's sector membership).

Pagination: cursor-based walks (see ``fetch_utils.paginate_cursor``) so open
market scans can cover more than a single 200-item page without raising quotas.
"""
import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

from ..data_errors import InsufficientDataError
from .base import DataConnector
from .fetch_utils import paginate_cursor, request_with_backoff
from .market_context import get_ticker_context_with_yfinance
from ..connector_cache import get_cached, set_cached

_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_TIMEOUT = 10
_PAGE_SIZE = int(os.environ.get("KALSHI_PAGE_SIZE", "200") or "200")
_MAX_PAGES = int(os.environ.get("KALSHI_MAX_PAGES", "3") or "3")

# Map tickers to Kalshi-searchable keywords and known series tickers.
_TICKER_MAP: Dict[str, Dict[str, Any]] = {
    "AAPL":  {"keywords": ["Apple", "AAPL", "Tim Cook"],       "series": ["KXAAPL", "KXAAPLCEO"]},
    "MSFT":  {"keywords": ["Microsoft", "MSFT"],                "series": ["KXMSFT"]},
    "GOOGL": {"keywords": ["Google", "Alphabet", "GOOGL"],      "series": []},
    "GOOG":  {"keywords": ["Google", "Alphabet"],               "series": []},
    "AMZN":  {"keywords": ["Amazon", "AMZN"],                   "series": ["KXAMZN"]},
    "TSLA":  {"keywords": ["Tesla", "TSLA"],                    "series": ["KXTSLA"]},
    "NVDA":  {"keywords": ["Nvidia", "NVDA"],                   "series": ["KXNVDA"]},
    "META":  {"keywords": ["Meta", "Facebook", "META"],         "series": ["KXMETA"]},
    "NFLX":  {"keywords": ["Netflix", "NFLX"],                  "series": []},
    "JPM":   {"keywords": ["JPMorgan", "JPM"],                  "series": []},
    "MRVL":  {"keywords": ["Marvell", "MRVL"],                  "series": []},
}

# Kalshi series tickers for index/macro markets
_INDEX_SERIES: Dict[str, List[str]] = {
    "S&P 500": ["KXSPY", "KXSPX"],
    "Nasdaq":  ["KXNASDAQ", "KXQQQ", "KXNDX"],
    "Dow Jones": ["KXDOW"],
}

_SECTOR_BLACKLIST = (
    "spacex", "anthropic", "bitcoin", "ethereum", "crypto", "cryptocurrency",
    "nasdaq private", "private market", "npm price", "solana", "dogecoin",
)


def _kalshi_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resp = request_with_backoff(
        "GET",
        f"{_BASE}{path}",
        params=params or {},
        timeout=_TIMEOUT,
    )
    body = resp.json()
    return body if isinstance(body, dict) else {}


def _paginate_kalshi_collection(
    path: str,
    *,
    base_params: Dict[str, Any],
    collection_key: str,
) -> Tuple[List[dict], int, int]:
    """Returns (items, attempted_requests, failed_requests)."""
    attempted = 0
    failed = 0
    collected: List[dict] = []

    def _fetch_page(cursor: Optional[str]) -> Tuple[List[dict], Optional[str]]:
        nonlocal attempted, failed
        attempted += 1
        params = dict(base_params)
        params["limit"] = min(_PAGE_SIZE, 1000)
        if cursor:
            params["cursor"] = cursor
        try:
            body = _kalshi_get(path, params)
            batch = body.get(collection_key) or []
            next_cursor = body.get("cursor") or None
            return batch, next_cursor if next_cursor else None
        except Exception:
            failed += 1
            return [], None

    collected = paginate_cursor(_fetch_page, max_pages=_MAX_PAGES)
    return collected, attempted, failed


def _fetch_kalshi_events(
    company_keywords: List[str],
    index_terms: List[str],
    series_tickers: List[str],
    index_series: List[str],
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Returns (events, attempted_requests, failed_requests)."""
    results: List[Dict[str, Any]] = []
    seen: set = set()
    attempted = 0
    failed = 0

    def _add(item: Dict[str, Any]):
        mid = item.get("url") or item.get("title")
        if mid and mid not in seen:
            seen.add(mid)
            results.append(item)

    def _passes_sector_filter(blob: str, is_sector: bool) -> bool:
        if not is_sector:
            return True
        return not any(bl_term in blob for bl_term in _SECTOR_BLACKLIST)

    # 1. Company-specific series (single page — series-scoped lists are small)
    for series_ticker in series_tickers:
        attempted += 1
        try:
            body = _kalshi_get(
                "/markets",
                {"series_ticker": series_ticker, "status": "open", "limit": 20},
            )
            for m in body.get("markets") or []:
                _add(_map_market(m, relevance_type="direct"))
        except Exception:
            failed += 1

    # 2. Index/sector series
    for series_ticker in index_series:
        attempted += 1
        try:
            body = _kalshi_get(
                "/markets",
                {"series_ticker": series_ticker, "status": "open", "limit": 10},
            )
            for m in body.get("markets") or []:
                _add(_map_market(m, relevance_type="sector"))
        except Exception:
            failed += 1

    # 3. Paginated open events scan (keyword filter client-side)
    ev_batch, ev_attempted, ev_failed = _paginate_kalshi_collection(
        "/events",
        base_params={"status": "open", "with_nested_markets": False},
        collection_key="events",
    )
    attempted += ev_attempted
    failed += ev_failed
    for ev in ev_batch:
        title = (ev.get("title") or "").lower()
        is_direct = any(kw.lower() in title for kw in company_keywords)
        is_sector = not is_direct and any(t.lower() in title for t in index_terms)
        if _passes_sector_filter(title, is_sector) and (is_direct or is_sector):
            _add(_map_event(ev, relevance_type="direct" if is_direct else "sector"))

    # 4. Paginated open markets scan (broader keyword search)
    mkt_batch, mkt_attempted, mkt_failed = _paginate_kalshi_collection(
        "/markets",
        base_params={"status": "open"},
        collection_key="markets",
    )
    attempted += mkt_attempted
    failed += mkt_failed
    for m in mkt_batch:
        blob = ((m.get("title") or "") + " " + (m.get("question") or "")).lower()
        is_direct = any(kw.lower() in blob for kw in company_keywords)
        is_sector = not is_direct and any(t.lower() in blob for t in index_terms)
        if _passes_sector_filter(blob, is_sector) and (is_direct or is_sector):
            _add(_map_market(m, relevance_type="direct" if is_direct else "sector"))

    return results, attempted, failed


def _map_market(m: dict, relevance_type: str = "direct") -> Dict[str, Any]:
    yes_bid = m.get("yes_bid")
    yes_ask = m.get("yes_ask")
    probability: Optional[float] = None
    if yes_bid is not None and yes_ask is not None:
        try:
            probability = round((float(yes_bid) + float(yes_ask)) / 2 / 100.0, 4)
        except (TypeError, ValueError):
            pass
    elif yes_bid is not None:
        try:
            probability = round(float(yes_bid) / 100.0, 4)
        except (TypeError, ValueError):
            pass

    return {
        "title": m.get("title") or m.get("question") or "",
        "market_question": m.get("question") or m.get("title") or "",
        "probability": probability,
        "volume": float(m.get("volume") or 0),
        "source": "Kalshi",
        "relevance_type": relevance_type,
        "url": f"https://kalshi.com/markets/{m.get('ticker', '')}",
        "close_time": m.get("close_time"),
    }


def _map_event(ev: dict, relevance_type: str = "direct") -> Dict[str, Any]:
    return {
        "title": ev.get("title") or ev.get("event_ticker") or "",
        "market_question": ev.get("title") or "",
        "probability": None,
        "volume": 0.0,
        "source": "Kalshi",
        "relevance_type": relevance_type,
        "url": f"https://kalshi.com/events/{ev.get('event_ticker', '')}",
        "close_time": ev.get("close_time"),
    }


class KalshiConnector(DataConnector):
    """Fetches open Kalshi prediction markets for a given stock ticker."""

    async def fetch_data(self, ticker: str = "AAPL", **kwargs) -> Dict[str, Any]:
        ticker_upper = ticker.upper()
        cached = get_cached("kalshi", ticker_upper)
        if cached is not None:
            return cached

        mapping = _TICKER_MAP.get(ticker_upper)
        if mapping:
            company_keywords = mapping["keywords"]
            series = mapping["series"]
        else:
            company_keywords = [ticker_upper]
            series = []

        # Get sector/index context
        ctx = await get_ticker_context_with_yfinance(ticker_upper)
        index_terms = ctx.get("index_search_terms") or []

        # Determine which index series to fetch
        idx_series: List[str] = []
        for idx_name in (ctx.get("indices") or []):
            idx_series.extend(_INDEX_SERIES.get(idx_name, []))

        events, attempted, failed = await asyncio.to_thread(
            _fetch_kalshi_events,
            company_keywords,
            index_terms,
            series,
            idx_series,
        )

        # Truthful-data contract: an empty list must mean "no markets exist",
        # never "every Kalshi request failed".
        if attempted > 0 and failed >= attempted and not events:
            raise InsufficientDataError(
                "kalshi",
                f"All {attempted} Kalshi API requests failed for {ticker_upper}.",
                ticker=ticker_upper,
                missing=["kalshi_events"],
            )

        # Sort direct first, then by volume within each group
        direct = sorted([e for e in events if e.get("relevance_type") == "direct"],
                        key=lambda x: x.get("volume") or 0, reverse=True)
        sector = sorted([e for e in events if e.get("relevance_type") == "sector"],
                        key=lambda x: x.get("volume") or 0, reverse=True)

        combined = direct[:8] + sector[:5]

        result: Dict[str, Any] = {
            "source": "Kalshi",
            "ticker": ticker_upper,
            "events": combined,
            "has_relevant_data": len(combined) > 0,
            "context": {
                "sector": ctx.get("sector"),
                "indices": ctx.get("indices"),
                "direct_count": len(direct),
                "sector_count": len(sector),
            },
        }
        set_cached("kalshi", result, ticker_upper)
        return result
