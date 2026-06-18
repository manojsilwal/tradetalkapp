"""
YouTube Data API v3 key resolution.

Production path (what we run today):
  1. ``YOUTUBE_API_KEY`` (dedicated YouTube Data API key)
  2. Optional ``YOUTUBE_API_KEY_2`` / ``GOOGLE_API_KEY`` when set and distinct
  3. Google News RSS ``site:youtube.com`` (in :mod:`social` — not this module)

``GEMINI_API_KEY`` is **not** used for YouTube: AI Studio keys are blocked from
YouTube Data API v3 even when the API is enabled on the project. Gemini remains
the LLM fallback only (see :mod:`gemini_llm`).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
_YT_PROBE_TIMEOUT_S = int(os.environ.get("YOUTUBE_API_TIMEOUT_S", "10"))
_YT_PROBE_MAX_RESULTS = 3

# (env var, source label) — order matters; Gemini intentionally omitted.
_YOUTUBE_KEY_SPECS: tuple[tuple[str, str], ...] = (
    ("YOUTUBE_API_KEY", "youtube_api_key"),
    ("youtube_api_key", "youtube_api_key"),
    ("YOUTUBE_API_KEY_2", "youtube_api_key_2"),
    ("GOOGLE_API_KEY", "google_api_key"),
)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def youtube_api_key_candidates() -> List[str]:
    """Unique YouTube-capable API keys in priority order."""
    seen: set[str] = set()
    out: List[str] = []
    gemini_val = _clean(os.environ.get("GEMINI_API_KEY"))
    for env_name, _label in _YOUTUBE_KEY_SPECS:
        val = _clean(os.environ.get(env_name))
        if not val or val in seen:
            continue
        # AI Studio / Gemini key reused as GOOGLE_API_KEY cannot call YouTube.
        if env_name == "GOOGLE_API_KEY" and gemini_val and val == gemini_val:
            continue
        seen.add(val)
        out.append(val)
    return out


def youtube_api_key_source_labels() -> List[str]:
    """Human-readable label per candidate key (never includes secret values)."""
    labels: List[str] = []
    seen: set[str] = set()
    gemini_val = _clean(os.environ.get("GEMINI_API_KEY"))
    for env_name, label in _YOUTUBE_KEY_SPECS:
        val = _clean(os.environ.get(env_name))
        if not val or val in seen:
            continue
        if env_name == "GOOGLE_API_KEY" and gemini_val and val == gemini_val:
            continue
        seen.add(val)
        labels.append(label)
    return labels


def primary_youtube_api_key() -> Optional[str]:
    keys = youtube_api_key_candidates()
    return keys[0] if keys else None


def _youtube_api_error_message(data: dict) -> Optional[str]:
    err = data.get("error")
    if not isinstance(err, dict):
        return None
    msg = err.get("message") or str(err)
    reasons = [
        r.get("reason")
        for r in (err.get("errors") or [])
        if isinstance(r, dict) and r.get("reason")
    ]
    if reasons:
        return f"{msg} ({', '.join(reasons)})"
    return str(msg)


def youtube_search_titles(
    api_key: str,
    query: str,
    *,
    limit: int = 20,
    timeout_s: int = _YT_PROBE_TIMEOUT_S,
    max_retries: int = 2,
    backoff_base_s: float = 1.0,
) -> Tuple[List[str], Optional[str]]:
    """
    YouTube Data API v3 ``search.list`` for video titles.

    Returns ``(titles, error)``. ``error`` is set when the key or API rejected
    the request (caller should try the next key). Empty titles with ``error=None``
    means the key worked but no videos matched.
    """
    params = urllib.parse.urlencode({
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(limit, 50),
        "order": "date",
        "relevanceLanguage": "en",
        "key": api_key,
    })
    url = f"{YOUTUBE_API_BASE}/search?{params}"
    req = urllib.request.Request(url)

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            raw = urllib.request.urlopen(req, timeout=timeout_s).read()
            data = json.loads(raw)
            err = _youtube_api_error_message(data)
            if err:
                return [], err

            titles: List[str] = []
            for item in (data.get("items") or [])[:limit]:
                title = (item.get("snippet") or {}).get("title")
                if title:
                    titles.append(title)
            return titles, None
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                err = _youtube_api_error_message(parsed)
                if err:
                    return [], err
            except Exception:
                pass
            last_exc = exc
            if exc.code in (403, 401, 400):
                return [], f"HTTP {exc.code}: {body[:200] or exc.reason}"
        except Exception as exc:
            last_exc = exc

        if attempt < max_retries:
            time.sleep(backoff_base_s * (2 ** attempt))

    return [], str(last_exc) if last_exc else "unknown error"


def _source_label_for_index(label: str, idx: int) -> str:
    if idx == 0:
        return "youtube_api_v3"
    if label == "youtube_api_key_2":
        return "youtube_api_v3_secondary"
    if label == "google_api_key":
        return "youtube_api_v3_google_fallback"
    return f"youtube_api_v3_{label}"


def fetch_youtube_titles_with_fallback(
    query: str,
    *,
    limit: int = 20,
) -> Tuple[List[str], str]:
    """Try each configured YouTube API key; return titles + source label."""
    keys = youtube_api_key_candidates()
    labels = youtube_api_key_source_labels()
    if not keys:
        return [], "none"

    for idx, api_key in enumerate(keys):
        label = labels[idx] if idx < len(labels) else f"key_{idx}"
        titles, err = youtube_search_titles(api_key, query, limit=limit)
        if err:
            logger.warning(
                "[YouTubeAPI] %s failed for query=%r: %s",
                label,
                query,
                err,
            )
            continue
        source = _source_label_for_index(label, idx)
        logger.info(
            "[YouTubeAPI] fetched %d titles via %s for query=%r",
            len(titles),
            source,
            query,
        )
        return titles, source

    return [], "none"


def probe_youtube_api_keys(ticker: str = "AAPL") -> Dict[str, Any]:
    """Diagnostic: test production YouTube keys + RSS (no secrets logged)."""
    query = f"{ticker.upper()} stock"
    keys = youtube_api_key_candidates()
    labels = youtube_api_key_source_labels()
    results: List[Dict[str, Any]] = []

    for idx, api_key in enumerate(keys):
        label = labels[idx] if idx < len(labels) else f"key_{idx}"
        titles, err = youtube_search_titles(api_key, query, limit=_YT_PROBE_MAX_RESULTS)
        results.append({
            "label": label,
            "ok": err is None,
            "title_count": len(titles),
            "sample_titles": titles[:2],
            "error": err,
        })

    rss_ok = False
    rss_count = 0
    rss_error: Optional[str] = None
    try:
        from .social import SocialSentimentConnector

        rss_titles = SocialSentimentConnector._fetch_rss_titles(
            f"{query} site:youtube.com",
            limit=_YT_PROBE_MAX_RESULTS,
        )
        rss_count = len(rss_titles)
        rss_ok = True
    except Exception as exc:
        rss_error = str(exc)

    any_yt_ok = any(r["ok"] and r["title_count"] > 0 for r in results)
    if results and results[0].get("ok"):
        recommended = "youtube_api_v3"
    elif any(r.get("ok") for r in results[1:]):
        recommended = _source_label_for_index(
            next(r["label"] for r in results[1:] if r.get("ok")),
            1,
        )
    elif rss_count:
        recommended = "rss"
    else:
        recommended = "degraded"

    gemini_key = _clean(os.environ.get("GEMINI_API_KEY"))
    gemini_note: Optional[str] = None
    if gemini_key:
        gemini_note = (
            "GEMINI_API_KEY is not used for YouTube Data API (AI Studio keys are "
            "blocked from youtube.googleapis.com). Use YOUTUBE_API_KEY + RSS fallback."
        )

    return {
        "ticker": ticker.upper(),
        "query": query,
        "production_path": ["youtube_api_v3", "rss"],
        "keys_tested": len(results),
        "key_results": results,
        "rss_fallback": {
            "ok": rss_ok,
            "title_count": rss_count,
            "error": rss_error,
        },
        "recommended_path": recommended,
        "any_youtube_api_ok": any(r["ok"] for r in results),
        "any_data": any_yt_ok or rss_count > 0,
        "gemini_key_note": gemini_note,
    }
