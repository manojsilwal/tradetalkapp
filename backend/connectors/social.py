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

logger = logging.getLogger(__name__)

_RSS_TIMEOUT_S: int = int(os.environ.get("SOCIAL_RSS_TIMEOUT_S", "10"))
_RSS_MAX_RETRIES: int = int(os.environ.get("SOCIAL_RSS_MAX_RETRIES", "2"))
_RSS_BACKOFF_BASE_S: float = float(os.environ.get("SOCIAL_RSS_BACKOFF_BASE_S", "1.0"))
_YT_API_MAX_RESULTS: int = int(os.environ.get("YOUTUBE_API_MAX_RESULTS", "20"))


class SocialSentimentConnector(DataConnector):
    """
    Fetches recent YouTube video titles via the **YouTube Data API v3**
    and blog headlines via the Google News RSS feed.

    YouTube source priority:
      1. YouTube Data API v3 (``YOUTUBE_API_KEY``)
      2. Google News RSS ``site:youtube.com`` when the API key fails or is unset

    ``GEMINI_API_KEY`` is reserved for LLM inference — AI Studio keys cannot call
    YouTube Data API v3, so social sentiment does not use them here.

    Graceful degradation: if both sources are unreachable the connector
    returns an empty-titles result (with ``degraded=True``) rather than
    raising ``InsufficientDataError``.  Social sentiment is a supplementary
    signal — it must never block the full swarm/debate/decision-terminal
    pipeline.
    """

    @staticmethod
    def _fetch_youtube_api_titles(ticker: str, limit: int = 20) -> tuple[List[str], str]:
        """Fetch recent video titles via YouTube Data API v3 (with key fallback)."""
        titles, source = fetch_youtube_titles_with_fallback(
            f"{ticker} stock",
            limit=limit,
        )
        return titles, source

    # ── Google News RSS (blogs + YouTube fallback) ───────────────────

    @staticmethod
    def _fetch_rss_titles(query: str, limit: int = 15) -> List[str]:
        """Fetch titles from Google News RSS with retry + exponential backoff.

        An empty list from a *successful* fetch is a real result ("no recent
        coverage") and is allowed through.  On total failure after retries,
        the exception is logged and an empty list is returned so the caller
        can degrade gracefully.
        """
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
        return []  # graceful degradation

    # ── Main fetch ───────────────────────────────────────────────────

    async def fetch_data(self, ticker: str = "SPY", **kwargs) -> Dict[str, Any]:
        ticker = kwargs.get("ticker", ticker).upper()
        cached = get_cached("social", ticker)
        if cached is not None:
            return cached

        async def get_blogs():
            return await asyncio.to_thread(
                SocialSentimentConnector._fetch_rss_titles,
                f"{ticker} stock blog", 20
            )

        async def get_yt():
            def _fetch():
                yt, yt_source = SocialSentimentConnector._fetch_youtube_api_titles(
                    ticker, limit=_YT_API_MAX_RESULTS,
                )
                if not yt and yt_source != "none":
                    logger.info(
                        "[SocialSentimentConnector] YouTube API keys exhausted, falling back to RSS for %s",
                        ticker,
                    )
                    yt = SocialSentimentConnector._fetch_rss_titles(
                        f"{ticker} stock site:youtube.com", limit=20,
                    )
                    yt_source = "rss_fallback"
                elif not yt:
                    yt = SocialSentimentConnector._fetch_rss_titles(
                        f"{ticker} stock site:youtube.com", limit=20,
                    )
                    yt_source = "rss"
                return {"youtube": yt, "yt_source": yt_source}
            return await asyncio.to_thread(_fetch)

        blogs, yt_res = await asyncio.gather(get_blogs(), get_yt())
        results = {"blogs": blogs, **yt_res}

        combined_titles = results["blogs"] + results["youtube"]
        degraded = len(combined_titles) == 0

        if degraded:
            logger.warning(
                "[SocialSentimentConnector] degraded result for %s — all sources unreachable, returning empty titles",
                ticker,
            )

        source_label = (
            "YouTube Data API v3 + Google News RSS"
            if results.get("yt_source", "").startswith("youtube_api_v3")
            else "Google News RSS (Blogs & YouTube)"
        )

        result = {
            "source": source_label,
            "ticker": ticker,
            "recent_titles": combined_titles,
            "degraded": degraded,
            "youtube_source": results.get("yt_source", "rss"),
            "counts": {
                "blogs": len(results["blogs"]),
                "youtube": len(results["youtube"]),
            },
        }
        # Only cache successful (non-degraded) results so fresh data is
        # fetched on the next request once the RSS feed recovers.
        if not degraded:
            set_cached("social", result, ticker)
        return result

