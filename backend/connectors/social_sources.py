"""
Free social / news headline fetchers for stock sentiment.

Each helper returns a list of short text strings (titles or message bodies).
Failures log and return [] — never raise.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import defusedxml.ElementTree as ET
from typing import List

logger = logging.getLogger(__name__)

_TIMEOUT_S = float(os.environ.get("SOCIAL_RSS_TIMEOUT_S", "10"))
_UA = os.environ.get(
    "SOCIAL_HTTP_USER_AGENT",
    "TradeTalk/1.0 (+https://github.com/manojsilwal/tradetalkapp)",
)
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _http_json(url: str, *, headers: dict | None = None) -> dict | None:
    hdrs = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        raw = urllib.request.urlopen(req, timeout=_TIMEOUT_S).read()
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("[SocialSources] JSON fetch failed url=%s err=%s", url[:80], exc)
        return None


def _http_xml(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        return urllib.request.urlopen(req, timeout=_TIMEOUT_S).read()
    except Exception as exc:
        logger.warning("[SocialSources] XML fetch failed url=%s err=%s", url[:80], exc)
        return None


def _ticker_in_text(text: str, ticker: str) -> bool:
    t = ticker.upper()
    blob = (text or "").upper()
    if t in blob:
        return True
    if f"${t}" in blob:
        return True
    return False


def fetch_yfinance_news_titles(ticker: str, limit: int = 15) -> List[str]:
    """Recent Yahoo Finance headlines for ``ticker`` via yfinance."""
    try:
        import yfinance as yf

        raw = yf.Ticker(ticker.upper()).news or []
    except Exception as exc:
        logger.warning("[SocialSources] yfinance news failed ticker=%s err=%s", ticker, exc)
        return []

    titles: List[str] = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not title and isinstance(item.get("content"), dict):
            title = item["content"].get("title")
        if title:
            titles.append(str(title).strip())
    logger.info("[SocialSources] yfinance news ticker=%s titles=%d", ticker, len(titles))
    return titles


def fetch_youtube_channel_rss_titles(ticker: str, limit: int = 20) -> List[str]:
    """
    Scan public YouTube channel Atom feeds (no API key / quota).
    Keeps entries whose title mentions the ticker.
    """
    from .youtube import FINANCE_CHANNELS

    ticker = ticker.upper()
    matched: List[str] = []

    for channel in FINANCE_CHANNELS:
        channel_id = (channel.get("id") or "").strip()
        if not channel_id:
            continue
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        raw = _http_xml(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            logger.warning(
                "[SocialSources] YouTube channel RSS parse failed channel=%s err=%s",
                channel.get("name"),
                exc,
            )
            continue

        for entry in root.findall("atom:entry", _ATOM_NS):
            title_el = entry.find("atom:title", _ATOM_NS)
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()
            if _ticker_in_text(title, ticker):
                matched.append(title)
            if len(matched) >= limit:
                break
        if len(matched) >= limit:
            break

    logger.info(
        "[SocialSources] YouTube channel RSS ticker=%s titles=%d",
        ticker,
        len(matched[:limit]),
    )
    return matched[:limit]


def fetch_reddit_titles(ticker: str, limit: int = 15) -> List[str]:
    """Recent Reddit post titles mentioning the ticker (public .json endpoint)."""
    if os.environ.get("SOCIAL_ENABLE_REDDIT", "1").strip().lower() in ("0", "false", "no"):
        return []

    ticker = ticker.upper()
    q = urllib.parse.quote(f"{ticker} OR ${ticker}")
    url = (
        f"https://www.reddit.com/search.json?q={q}"
        f"&sort=new&t=week&limit={min(limit, 25)}&type=link"
    )
    ua = os.environ.get("SOCIAL_REDDIT_USER_AGENT", _UA)
    data = _http_json(url, headers={"User-Agent": ua})
    if not data:
        return []

    children = (data.get("data") or {}).get("children") or []
    titles: List[str] = []
    for child in children:
        post = child.get("data") if isinstance(child, dict) else None
        if not isinstance(post, dict):
            continue
        title = (post.get("title") or "").strip()
        if title and _ticker_in_text(title, ticker):
            titles.append(title)
        if len(titles) >= limit:
            break

    logger.info("[SocialSources] Reddit ticker=%s titles=%d", ticker, len(titles))
    return titles


def fetch_stocktwits_titles(ticker: str, limit: int = 20) -> List[str]:
    """Recent Stocktwits messages for ``$TICKER`` (public symbol stream)."""
    if os.environ.get("SOCIAL_ENABLE_STOCKTWITS", "1").strip().lower() in ("0", "false", "no"):
        return []

    ticker = ticker.upper()
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    data = _http_json(url)
    if not data:
        return []

    messages = data.get("messages") or []
    titles: List[str] = []
    for msg in messages[:limit]:
        if not isinstance(msg, dict):
            continue
        body = re.sub(r"\s+", " ", (msg.get("body") or "").strip())
        if body:
            titles.append(body[:280])
    logger.info("[SocialSources] Stocktwits ticker=%s messages=%d", ticker, len(titles))
    return titles
