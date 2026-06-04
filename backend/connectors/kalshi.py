"""
Kalshi prediction market connector.

Uses the public Kalshi Trade API v2 (no auth required for market data).
Fetches events by keyword search, tagging results as "direct" (company-specific)
or "sector" (index/ETF-level bets relevant to the ticker's sector membership).
"""
import asyncio
import requests
from typing import Any, Dict, List, Optional
from .base import DataConnector
from .market_context import get_ticker_context_with_yfinance
from ..connector_cache import get_cached, set_cached

_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_TIMEOUT = 8

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


def _fetch_kalshi_events(
    company_keywords: List[str],
    index_terms: List[str],
    series_tickers: List[str],
    index_series: List[str],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(item: Dict[str, Any]):
        mid = item.get("url") or item.get("title")
        if mid and mid not in seen:
            seen.add(mid)
            results.append(item)

    # 1. Company-specific series
    for series_ticker in series_tickers:
        try:
            r = requests.get(
                f"{_BASE}/markets",
                params={"series_ticker": series_ticker, "status": "open", "limit": 20},
                timeout=_TIMEOUT,
            )
            if r.ok:
                for m in r.json().get("markets") or []:
                    mapped = _map_market(m, relevance_type="direct")
                    _add(mapped)
        except Exception:
            pass

    # 2. Index/sector series
    for series_ticker in index_series:
        try:
            r = requests.get(
                f"{_BASE}/markets",
                params={"series_ticker": series_ticker, "status": "open", "limit": 10},
                timeout=_TIMEOUT,
            )
            if r.ok:
                for m in r.json().get("markets") or []:
                    mapped = _map_market(m, relevance_type="sector")
                    _add(mapped)
        except Exception:
            pass

    # 3. Scan open events by title keyword (company + index)
    all_kw = list(company_keywords) + list(index_terms)
    try:
        r = requests.get(
            f"{_BASE}/events",
            params={"status": "open", "limit": 200, "with_nested_markets": False},
            timeout=_TIMEOUT,
        )
        if r.ok:
            for ev in r.json().get("events") or []:
                title = (ev.get("title") or "").lower()
                is_direct = any(kw.lower() in title for kw in company_keywords)
                is_sector = not is_direct and any(t.lower() in title for t in index_terms)
                if is_sector:
                    sector_blacklist = ["spacex", "anthropic", "bitcoin", "ethereum", "crypto", "cryptocurrency", "nasdaq private", "private market", "npm price", "solana", "dogecoin"]
                    if any(bl_term in title for bl_term in sector_blacklist):
                        is_sector = False
                if is_direct or is_sector:
                    mapped = _map_event(ev, relevance_type="direct" if is_direct else "sector")
                    _add(mapped)
    except Exception:
        pass

    # 4. Scan open markets (broader search)
    try:
        r = requests.get(
            f"{_BASE}/markets",
            params={"status": "open", "limit": 200},
            timeout=_TIMEOUT,
        )
        if r.ok:
            for m in r.json().get("markets") or []:
                blob = ((m.get("title") or "") + " " + (m.get("question") or "")).lower()
                is_direct = any(kw.lower() in blob for kw in company_keywords)
                is_sector = not is_direct and any(t.lower() in blob for t in index_terms)
                if is_sector:
                    sector_blacklist = ["spacex", "anthropic", "bitcoin", "ethereum", "crypto", "cryptocurrency", "nasdaq private", "private market", "npm price", "solana", "dogecoin"]
                    if any(bl_term in blob for bl_term in sector_blacklist):
                        is_sector = False
                if is_direct or is_sector:
                    mapped = _map_market(m, relevance_type="direct" if is_direct else "sector")
                    _add(mapped)
    except Exception:
        pass

    return results


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

        events = await asyncio.to_thread(
            _fetch_kalshi_events,
            company_keywords,
            index_terms,
            series,
            idx_series,
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
