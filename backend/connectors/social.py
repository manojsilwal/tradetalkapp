import asyncio
import logging
import os
import time
import urllib.request
import urllib.parse
import defusedxml.ElementTree as ET
from typing import Dict, Any, List
from .base import DataConnector
from ..connector_cache import get_cached, set_cached
from .youtube_keys import fetch_youtube_titles_with_fallback
from . import social_sources

logger = logging.getLogger(__name__)

_RSS_TIMEOUT_S: int = int(os.environ.get("SOCIAL_RSS_TIMEOUT_S", "10"))
_RSS_MAX_RETRIES: int = int(os.environ.get("SOCIAL_RSS_MAX_RETRIES", "2"))
_RSS_BACKOFF_BASE_S: float = float(os.environ.get("SOCIAL_RSS_BACKOFF_BASE_S", "1.0"))
_YT_API_MAX_RESULTS: int = int(os.environ.get("YOUTUBE_API_MAX_RESULTS", "20"))
_YFINANCE_NEWS_LIMIT: int = int(os.environ.get("SOCIAL_YFINANCE_NEWS_LIMIT", "15"))
_SOCIAL_REDDIT_LIMIT: int = int(os.environ.get("SOCIAL_REDDIT_LIMIT", "15"))
_SOCIAL_STOCKTWITS_LIMIT: int = int(os.environ.get("SOCIAL_STOCKTWITS_LIMIT", "20"))


def _merge_unique_titles(*groups: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for group in groups:
        for title in group:
            key = (title or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(title.strip())
    return out


class SocialSentimentConnector(DataConnector):
    """
    Aggregates free headline sources for retail / media sentiment.

    Priority stack (per bucket):
      **Video / YouTube**
        1. YouTube Data API v3 (``YOUTUBE_API_KEY``)
        2. Google News RSS ``site:youtube.com``
        3. YouTube public channel Atom feeds (finance channels, no quota)

      **News / blogs**
        2. Google News RSS ``{ticker} stock blog``
        3. yfinance news headlines

      **Social**
        5. Reddit search (public JSON, no key)
        5. Stocktwits symbol stream (public, no key)

    Graceful degradation: returns ``degraded=True`` with empty titles only when
    every source fails — never raises ``InsufficientDataError``.
    """

    @staticmethod
    def _fetch_youtube_api_titles(ticker: str, limit: int = 20) -> tuple[List[str], str]:
        titles, source = fetch_youtube_titles_with_fallback(
            f"{ticker} stock",
            limit=limit,
        )
        return titles, source

    @staticmethod
    def _resolve_youtube_titles(ticker: str, limit: int = 20) -> tuple[List[str], str]:
        yt, yt_source = SocialSentimentConnector._fetch_youtube_api_titles(ticker, limit=limit)

        if not yt:
            yt = SocialSentimentConnector._fetch_rss_titles(
                f"{ticker} stock site:youtube.com",
                limit=limit,
            )
            if yt:
                yt_source = "google_news_youtube_rss"

        if not yt:
            yt = social_sources.fetch_youtube_channel_rss_titles(ticker, limit=limit)
            if yt:
                yt_source = "youtube_channel_rss"

        if not yt and yt_source == "none":
            yt_source = "none"
        elif not yt:
            yt_source = "none"

        return yt, yt_source

    @staticmethod
    def _fetch_rss_titles(query: str, limit: int = 15) -> List[str]:
        q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        last_exc: Exception | None = None
        for attempt in range(_RSS_MAX_RETRIES + 1):
            try:
                html = urllib.request.urlopen(req, timeout=_RSS_TIMEOUT_S).read()
                root = ET.fromstring(html)
                titles: list[str] = []
                for i in root.findall(".//item")[:limit]:
                    title_elem = i.find("title")
                    if title_elem is not None and title_elem.text:
                        raw_title = title_elem.text
                        clean = raw_title.split(" - ")[0] if " - " in raw_title else raw_title
                        titles.append(clean)
                return titles
            except Exception as exc:
                last_exc = exc
                if attempt < _RSS_MAX_RETRIES:
                    backoff = _RSS_BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(
                        "[SocialRSS] attempt %d/%d for query=%r failed (%s), retrying in %.1fs…",
                        attempt + 1, _RSS_MAX_RETRIES + 1, query, exc, backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.warning(
                        "[SocialRSS] all %d attempts exhausted for query=%r: %s",
                        _RSS_MAX_RETRIES + 1, query, last_exc,
                    )
        return []

    async def fetch_data(self, ticker: str = "SPY", **kwargs) -> Dict[str, Any]:
        ticker = kwargs.get("ticker", ticker).upper()
        cached = get_cached("social", ticker)
        if cached is not None:
            return cached

        async def get_blogs():
            return await asyncio.to_thread(
                SocialSentimentConnector._fetch_rss_titles,
                f"{ticker} stock blog",
                20,
            )

        async def get_youtube():
            return await asyncio.to_thread(
                SocialSentimentConnector._resolve_youtube_titles,
                ticker,
                _YT_API_MAX_RESULTS,
            )

        async def get_yfinance():
            return await asyncio.to_thread(
                social_sources.fetch_yfinance_news_titles,
                ticker,
                _YFINANCE_NEWS_LIMIT,
            )

        async def get_reddit():
            return await asyncio.to_thread(
                social_sources.fetch_reddit_titles,
                ticker,
                _SOCIAL_REDDIT_LIMIT,
            )

        async def get_stocktwits():
            return await asyncio.to_thread(
                social_sources.fetch_stocktwits_titles,
                ticker,
                _SOCIAL_STOCKTWITS_LIMIT,
            )

        blogs, (youtube, yt_source), yfinance_news, reddit, stocktwits = await asyncio.gather(
            get_blogs(),
            get_youtube(),
            get_yfinance(),
            get_reddit(),
            get_stocktwits(),
        )

        combined_titles = _merge_unique_titles(
            youtube,
            blogs,
            yfinance_news,
            reddit,
            stocktwits,
        )
        degraded = len(combined_titles) == 0

        if degraded:
            logger.warning(
                "[SocialSentimentConnector] degraded result for %s — all sources empty",
                ticker,
            )

        counts = {
            "youtube": len(youtube),
            "blogs": len(blogs),
            "yfinance_news": len(yfinance_news),
            "reddit": len(reddit),
            "stocktwits": len(stocktwits),
        }

        active_sources = [k for k, v in counts.items() if v > 0]
        source_label = (
            "Multi-source social sentiment ("
            + ", ".join(active_sources)
            + ")"
            if active_sources
            else "No social/news sources available"
        )

        result = {
            "source": source_label,
            "ticker": ticker,
            "recent_titles": combined_titles,
            "degraded": degraded,
            "youtube_source": yt_source,
            "counts": counts,
        }
        if not degraded:
            set_cached("social", result, ticker)
        return result
