import asyncio
import json
import logging
import os
import time
import urllib.request
import urllib.parse
import defusedxml.ElementTree as ET
from typing import Dict, Any, List
from .base import DataConnector
from ..connector_cache import get_cached, set_cached

logger = logging.getLogger(__name__)

# Configurable via env: timeout per request, max retries, initial backoff.
_RSS_TIMEOUT_S: int = int(os.environ.get("SOCIAL_RSS_TIMEOUT_S", "10"))
_RSS_MAX_RETRIES: int = int(os.environ.get("SOCIAL_RSS_MAX_RETRIES", "2"))
_RSS_BACKOFF_BASE_S: float = float(os.environ.get("SOCIAL_RSS_BACKOFF_BASE_S", "1.0"))

_YT_API_TIMEOUT_S: int = int(os.environ.get("YOUTUBE_API_TIMEOUT_S", "10"))
_YT_API_MAX_RESULTS: int = int(os.environ.get("YOUTUBE_API_MAX_RESULTS", "20"))


def _get_youtube_api_key() -> str | None:
    """Return the YouTube Data API v3 key, or None if not configured."""
    return os.environ.get("youtube_api_key") or os.environ.get("YOUTUBE_API_KEY") or None


class SocialSentimentConnector(DataConnector):
    """
    Fetches recent YouTube video titles via the **YouTube Data API v3**
    and blog headlines via the Google News RSS feed.

    YouTube source priority:
      1. YouTube Data API v3 (if ``youtube_api_key`` env var is set)
      2. Google News RSS ``site:youtube.com`` (legacy fallback)

    Graceful degradation: if both sources are unreachable the connector
    returns an empty-titles result (with ``degraded=True``) rather than
    raising ``InsufficientDataError``.  Social sentiment is a supplementary
    signal — it must never block the full swarm/debate/decision-terminal
    pipeline.
    """

    # ── YouTube Data API v3 ──────────────────────────────────────────

    @staticmethod
    def _fetch_youtube_api_titles(ticker: str, limit: int = 20) -> List[str]:
        """Fetch recent video titles from YouTube Data API v3.

        Returns an empty list on any failure (logged, never raises).
        """
        api_key = _get_youtube_api_key()
        if not api_key:
            return []  # caller will fall back to RSS

        params = urllib.parse.urlencode({
            "part": "snippet",
            "q": f"{ticker} stock",
            "type": "video",
            "maxResults": min(limit, 50),
            "order": "date",
            "relevanceLanguage": "en",
            "key": api_key,
        })
        url = f"https://www.googleapis.com/youtube/v3/search?{params}"
        req = urllib.request.Request(url)

        for attempt in range(_RSS_MAX_RETRIES + 1):
            try:
                raw = urllib.request.urlopen(req, timeout=_YT_API_TIMEOUT_S).read()
                data = json.loads(raw)

                if "error" in data:
                    err_msg = data["error"].get("message", str(data["error"]))
                    logger.warning("[YouTubeAPI] API error for %s: %s", ticker, err_msg)
                    return []  # bad key / quota — don't retry

                titles: list[str] = []
                for item in data.get("items", [])[:limit]:
                    title = item.get("snippet", {}).get("title")
                    if title:
                        titles.append(title)
                logger.info(
                    "[YouTubeAPI] fetched %d titles for %s via Data API v3",
                    len(titles), ticker,
                )
                return titles
            except Exception as exc:
                if attempt < _RSS_MAX_RETRIES:
                    backoff = _RSS_BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(
                        "[YouTubeAPI] attempt %d/%d for %s failed (%s), retrying in %.1fs…",
                        attempt + 1, _RSS_MAX_RETRIES + 1, ticker, exc, backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.warning(
                        "[YouTubeAPI] all %d attempts exhausted for %s: %s",
                        _RSS_MAX_RETRIES + 1, ticker, exc,
                    )
        return []

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

        def get_all_social():
            # Blogs: always via Google News RSS
            blogs = SocialSentimentConnector._fetch_rss_titles(
                f"{ticker} stock blog", limit=20,
            )

            # YouTube: prefer Data API v3, fall back to RSS
            yt_source = "youtube_api_v3"
            yt = SocialSentimentConnector._fetch_youtube_api_titles(ticker, limit=_YT_API_MAX_RESULTS)
            if not yt and _get_youtube_api_key():
                # API key set but fetch failed — try RSS fallback
                logger.info("[SocialSentimentConnector] YouTube API returned empty, falling back to RSS for %s", ticker)
                yt = SocialSentimentConnector._fetch_rss_titles(
                    f"{ticker} stock site:youtube.com", limit=20,
                )
                yt_source = "rss_fallback"
            elif not yt:
                # No API key — use RSS
                yt = SocialSentimentConnector._fetch_rss_titles(
                    f"{ticker} stock site:youtube.com", limit=20,
                )
                yt_source = "rss"

            return {"blogs": blogs, "youtube": yt, "yt_source": yt_source}

        results = await asyncio.to_thread(get_all_social)

        combined_titles = results["blogs"] + results["youtube"]
        degraded = len(combined_titles) == 0

        if degraded:
            logger.warning(
                "[SocialSentimentConnector] degraded result for %s — all sources unreachable, returning empty titles",
                ticker,
            )

        source_label = (
            "YouTube Data API v3 + Google News RSS"
            if results.get("yt_source") == "youtube_api_v3"
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

