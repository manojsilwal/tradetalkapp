import asyncio
import json
import os
import yfinance as yf
from typing import Any, Dict, List, Optional, Tuple
from ..data_errors import InsufficientDataError
from .base import DataConnector
from .fetch_utils import paginate_cursor, paginate_offset, request_with_backoff
from .market_context import get_ticker_context_with_yfinance
from ..connector_cache import get_cached, set_cached

# Static keyword map — company name tokens used for relevance matching.
TICKER_KEYWORDS = {
    "AAPL": ["Apple", "AAPL", "iPhone", "Tim Cook"],
    "MSFT": ["Microsoft", "MSFT"],
    "GOOG": ["Google", "Alphabet", "GOOGL"],
    "GOOGL": ["Google", "Alphabet", "GOOGL"],
    "AMZN": ["Amazon", "AMZN"],
    "META": ["Meta", "Facebook", "META"],
    "TSLA": ["Tesla", "TSLA", "Elon Musk"],
    "NVDA": ["Nvidia", "NVDA"],
    "GME": ["GameStop", "GME"],
    "AMC": ["AMC"],
    "MSTR": ["MicroStrategy", "MSTR"],
    "NFLX": ["Netflix", "NFLX"],
    "JPM": ["JPMorgan", "JPM"],
    "MRVL": ["Marvell", "MRVL"],
}

# Tags that reliably contain stock/equity prediction markets on Polymarket.
_STOCK_TAGS = ("stocks", "equities", "finance", "financials")

_GAMMA_BASE = "https://gamma-api.polymarket.com"
_TAG_PAGE_SIZE = int(os.environ.get("POLYMARKET_PAGE_SIZE", "100") or "100")
_TAG_MAX_PAGES = int(os.environ.get("POLYMARKET_MAX_PAGES", "5") or "5")


def _fetch_tag_events_paginated(tag: str) -> List[dict]:
    """
    Walk all open events for a tag using keyset pagination when available,
    falling back to offset pagination on the legacy ``/events`` endpoint.
    """
    keyset_url = f"{_GAMMA_BASE}/events/keyset"

    def _fetch_keyset_page(after_cursor: Optional[str]) -> Tuple[List[dict], Optional[str]]:
        params: Dict[str, Any] = {
            "tag_slug": tag,
            "limit": min(_TAG_PAGE_SIZE, 500),
            "closed": "false",
        }
        if after_cursor:
            params["after_cursor"] = after_cursor
        resp = request_with_backoff("GET", keyset_url, params=params, timeout=10)
        body = resp.json()
        if isinstance(body, dict):
            batch = body.get("events") or []
            next_cursor = body.get("next_cursor") or None
            return batch, next_cursor if next_cursor else None
        if isinstance(body, list):
            return body, None
        return [], None

    try:
        return paginate_cursor(_fetch_keyset_page, max_pages=_TAG_MAX_PAGES)
    except Exception as keyset_err:
        logger = __import__("logging").getLogger(__name__)
        logger.debug("[Polymarket] keyset pagination failed for tag=%s: %s", tag, keyset_err)

    def _fetch_offset_page(offset: int) -> List[dict]:
        resp = request_with_backoff(
            "GET",
            f"{_GAMMA_BASE}/events",
            params={
                "tag_slug": tag,
                "limit": _TAG_PAGE_SIZE,
                "offset": offset,
                "closed": "false",
            },
            timeout=10,
        )
        batch = resp.json()
        return batch if isinstance(batch, list) else []

    return paginate_offset(_fetch_offset_page, page_size=_TAG_PAGE_SIZE, max_pages=_TAG_MAX_PAGES)


class PolymarketConnector(DataConnector):
    """
    Fetches Polymarket prediction markets relevant to a ticker.

    Strategy:
      1. Resolve company-name keywords (static map → yfinance fallback).
      2. Fetch events from stock/equity tags concurrently.
      3. Score each event: "direct" (company-specific) or "sector" (index/ETF-level).
      4. Return both categories, labelled, sorted by volume.
    """

    async def fetch_data(self, ticker: str = "GME", **kwargs) -> Dict[str, Any]:
        ticker_upper = ticker.upper()
        cached = get_cached("polymarket", ticker_upper)
        if cached is not None:
            return cached

        # 1. Resolve company keywords
        keywords = TICKER_KEYWORDS.get(ticker_upper)
        keyword_resolution = "static_map"
        if not keywords:
            keyword_resolution = "yfinance"
            try:
                info = await asyncio.to_thread(lambda: yf.Ticker(ticker_upper).info)
                company_name = info.get("shortName") or info.get("longName") or ""
                first_word = company_name.split()[0] if company_name else ""
                keywords = [first_word, ticker_upper] if first_word else [ticker_upper]
            except Exception:
                keywords = [ticker_upper]

        # 2. Get sector/index context for this ticker
        ctx = await get_ticker_context_with_yfinance(ticker_upper)
        index_terms = ctx.get("index_search_terms") or []

        # 3. Fetch events from all relevant tags concurrently. A failed tag
        # fetch raises — an empty events list must mean "no markets exist",
        # never "the API call failed".
        tasks = [
            asyncio.to_thread(_fetch_tag_events_paginated, tag) for tag in _STOCK_TAGS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed_tags = [
            tag for tag, batch in zip(_STOCK_TAGS, results) if isinstance(batch, Exception)
        ]
        if failed_tags:
            raise InsufficientDataError(
                "polymarket",
                "Polymarket event fetch failed for tags: " + ", ".join(failed_tags),
                ticker=ticker_upper,
                missing=[f"polymarket_tag:{t}" for t in failed_tags],
            )

        # Merge and deduplicate by event id
        seen_ids: set = set()
        all_events: List[dict] = []
        for batch in results:
            if isinstance(batch, list):
                for ev in batch:
                    eid = ev.get("id") or ev.get("slug") or ev.get("title")
                    if eid not in seen_ids:
                        seen_ids.add(eid)
                        all_events.append(ev)

        # 4. Score events: direct (company) or sector (index/ETF)
        direct_events: List[Dict[str, Any]] = []
        sector_events: List[Dict[str, Any]] = []

        for e in all_events:
            title = e.get("title", "")
            description = e.get("description", "") or ""
            combined_text = (title + " " + description).lower()

            is_direct = any(kw.lower() in combined_text for kw in keywords)
            is_sector = (
                not is_direct
                and bool(index_terms)
                and any(term.lower() in combined_text for term in index_terms)
            )

            if is_sector:
                sector_blacklist = ["spacex", "anthropic", "bitcoin", "ethereum", "crypto", "cryptocurrency", "nasdaq private", "private market", "npm price", "solana", "dogecoin"]
                if any(bl_term in combined_text for bl_term in sector_blacklist):
                    is_sector = False

            if not is_direct and not is_sector:
                continue

            mapped = _extract_best_market(e)
            mapped["relevance_type"] = "direct" if is_direct else "sector"
            mapped["sector_context"] = ctx.get("sector")
            mapped["source"] = "Polymarket"

            if is_direct:
                direct_events.append(mapped)
            else:
                sector_events.append(mapped)

        # Sort each group by volume desc
        direct_events.sort(key=lambda x: x.get("volume") or 0, reverse=True)
        sector_events.sort(key=lambda x: x.get("volume") or 0, reverse=True)

        # Combine: direct first, then sector context
        all_relevant = direct_events[:8] + sector_events[:5]

        result = {
            "source": "Polymarket",
            "keyword_resolution": keyword_resolution,
            "ticker": ticker_upper,
            "events": all_relevant,
            "has_relevant_data": len(all_relevant) > 0,
            "context": {
                "sector": ctx.get("sector"),
                "indices": ctx.get("indices"),
                "direct_count": len(direct_events),
                "sector_count": len(sector_events),
            },
        }
        set_cached("polymarket", result, ticker_upper)
        return result


def _extract_best_market(e: dict) -> Dict[str, Any]:
    """Pick the highest-volume market from an event and extract its probability."""
    title = e.get("title", "")
    description = e.get("description", "") or ""
    markets = e.get("markets") or []
    best_market = None
    best_prob = None
    best_volume = 0.0
    for m in markets:
        vol = float(m.get("volumeNum") or 0)
        prices_str = m.get("outcomePrices")
        if prices_str:
            try:
                prices = json.loads(prices_str)
                yes_price = float(prices[0]) if prices else None
                if yes_price is not None and (best_market is None or vol > best_volume):
                    best_market = m
                    best_prob = round(yes_price, 4)
                    best_volume = vol
            except (ValueError, IndexError):
                pass

    return {
        "title": title,
        "description": description[:200] if description else "",
        "probability": best_prob,
        "volume": round(best_volume, 2),
        "market_question": best_market.get("question") if best_market else None,
        "url": f"https://polymarket.com/event/{e.get('slug') or ''}",
    }
