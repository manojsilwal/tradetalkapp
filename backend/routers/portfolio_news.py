"""
Portfolio News & Impact Feed — GET /portfolio/news?tickers=AAPL,MSFT,GOOGL

Fetches up to 5 recent yfinance headlines per ticker, filters to credible publishers,
classifies each item's sentiment + impact via LLM, deduplicates, and returns a
time-sorted feed capped at 20 items.  Results are cached per ticker-set for 15 min.
"""
import asyncio
import hashlib
import logging
import time
from typing import Optional

import yfinance as yf
from fastapi import APIRouter, Query

from ..deps import llm_client

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = logging.getLogger(__name__)

# ── Publisher whitelist ────────────────────────────────────────────────────────
# Case-insensitive substring matching against the publisher field from yfinance.
CREDIBLE_PUBLISHERS: frozenset[str] = frozenset({
    "reuters",
    "bloomberg",
    "wall street journal",
    "wsj",
    "associated press",
    "ap news",
    "marketwatch",
    "cnbc",
    "financial times",
    "ft.com",
    "seeking alpha",
    "sec",
    "barron",
    "yahoo finance",
    "business wire",
    "businesswire",
    "pr newswire",
    "prnewswire",
    "globenewswire",
    "benzinga",
    "the motley fool",
    "motley fool",
    "investor's business daily",
    "ibd",
})

# ── In-memory cache (no persistence — intentional for a 15-min TTL) ────────────
_news_cache: dict = {}
_CACHE_TTL_SECONDS = 15 * 60
_MAX_NEWS_PER_TICKER = 5
_FEED_CAP = 20
_TICKER_LIMIT = 20


def _cache_key(tickers: list[str]) -> str:
    return ",".join(sorted(t.upper() for t in tickers))


def is_credible_publisher(publisher: str) -> bool:
    """Return True when publisher matches any entry in the credible-sources whitelist."""
    pub_lower = (publisher or "").lower()
    return any(credible in pub_lower for credible in CREDIBLE_PUBLISHERS)


def _fetch_yf_news_sync(ticker: str) -> list[dict]:
    """Synchronous yfinance fetch — called inside asyncio.to_thread."""
    try:
        t = yf.Ticker(ticker)
        raw = t.news or []
        return list(raw[:_MAX_NEWS_PER_TICKER])
    except Exception as exc:
        logger.warning("[portfolio_news] yfinance error for %s: %s", ticker, exc)
        return []


async def _classify_news_item(headline: str, ticker: str) -> dict:
    """
    LLM call: classify headline sentiment + 1-2 sentence impact explanation.
    Prompt is kept < 150 tokens to avoid slow calls.
    Always returns a safe dict even on LLM failure.
    """
    prompt = (
        f'Headline: "{headline[:200]}"\n'
        f"Ticker: {ticker}\n\n"
        "Respond with JSON only:\n"
        '{"sentiment":"positive|negative|neutral","impact":"1-2 sentence investor impact"}'
    )
    try:
        result = await llm_client.generate("news_impact_classifier", prompt)
        sentiment = result.get("sentiment", "neutral")
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        impact: Optional[str] = result.get("impact") or None
        return {"sentiment": sentiment, "impact": impact}
    except Exception as exc:
        logger.warning(
            "[portfolio_news] LLM classify failed for '%s': %s", headline[:60], exc
        )
        return {"sentiment": "neutral", "impact": None}


async def _process_ticker(ticker: str, seen: set) -> list[dict]:
    """Fetch, filter, and classify news for a single ticker."""
    raw_news = await asyncio.to_thread(_fetch_yf_news_sync, ticker)

    valid: list[tuple] = []
    for item in raw_news:
        publisher = item.get("publisher", "")
        if not is_credible_publisher(publisher):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        h = hashlib.md5(title.lower().encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        valid.append((item, h))

    if not valid:
        return []

    classifications = await asyncio.gather(
        *[_classify_news_item(item["title"], ticker) for item, _ in valid],
        return_exceptions=True,
    )

    results: list[dict] = []
    for (item, _), clf in zip(valid, classifications):
        if isinstance(clf, Exception):
            clf = {"sentiment": "neutral", "impact": None}
        results.append(
            {
                "ticker": ticker.upper(),
                "title": item.get("title", ""),
                "publisher": item.get("publisher", ""),
                "link": item.get("link", ""),
                "published_at": item.get("providerPublishTime"),  # Unix timestamp or None
                "sentiment": clf.get("sentiment", "neutral"),
                "impact": clf.get("impact"),
            }
        )
    return results


async def _build_news_feed(tickers: list[str]) -> list[dict]:
    """Fetch + classify news for all tickers concurrently, deduplicate, and sort."""
    seen_hashes: set = set()
    ticker_results = await asyncio.gather(
        *[_process_ticker(t, seen_hashes) for t in tickers],
        return_exceptions=True,
    )
    all_items: list[dict] = []
    for res in ticker_results:
        if isinstance(res, Exception):
            logger.warning("[portfolio_news] ticker task error: %s", res)
            continue
        all_items.extend(res)

    # Most-recent first; items without timestamp go last
    all_items.sort(key=lambda x: x.get("published_at") or 0, reverse=True)
    return all_items[:_FEED_CAP]


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.get("/news")
async def get_portfolio_news(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT"),
):
    """
    Return a deduplicated, impact-classified news feed for the given portfolio tickers.
    Cached per ticker-set for 15 minutes.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return {"items": [], "cached": False}

    ticker_list = ticker_list[:_TICKER_LIMIT]

    key = _cache_key(ticker_list)
    now = time.time()
    cached_entry = _news_cache.get(key)
    if cached_entry and (now - cached_entry["ts"]) < _CACHE_TTL_SECONDS:
        return {"items": cached_entry["data"], "cached": True}

    items = await _build_news_feed(ticker_list)
    _news_cache[key] = {"ts": now, "data": items}
    return {"items": items, "cached": False}
