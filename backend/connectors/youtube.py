"""
YouTube Finance Connector — fetches latest videos from top finance channels.

Requires ``YOUTUBE_API_KEY`` for YouTube Data API v3 (free tier: 10,000 units/day).
When the key fails, social sentiment falls back to Google News RSS — not ``GEMINI_API_KEY``.

Quota strategy: use each channel's **uploads playlist** via ``playlistItems.list``
(1 unit/call) instead of ``search.list`` (100 units/call). For six channels that
is ~12 units per ingestion run vs ~600 previously — see §5.10 in ARCHITECTURE.md.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .youtube_keys import youtube_api_key_candidates

logger = logging.getLogger(__name__)

# Top finance / investing channels
FINANCE_CHANNELS = [
    {"id": "UCvM5YYWwfLwTyaKZPMCIRwg", "name": "CNBC"},
    {"id": "UCIALMKvObZNtJ6AmdCLP_iQ", "name": "Bloomberg"},
    {"id": "UCa-_0SQaFW5CompEZ8krOyQ", "name": "Graham Stephan"},
    {"id": "UC3vIljCBzwFU8TqVnFQQKZg", "name": "Andrei Jikh"},
    {"id": "UCnMn36GT_H0X-w5_ckLtlgQ", "name": "Meet Kevin"},
    {"id": "UCOWT4bGPQTnAe5OmGq9cX3g", "name": "Patrick Boyle"},
]

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
_PLAYLIST_PAGE_SIZE = 50


def channel_uploads_playlist_id(channel_id: str) -> str:
    """
    Derive the uploads playlist ID from a channel ID (UC… → UU…).

    YouTube convention: replace the leading ``UC`` with ``UU`` for the
    channel's "uploads" playlist. Fallback: ``channels.list`` when needed.
    """
    cid = (channel_id or "").strip()
    if cid.startswith("UC") and len(cid) > 2:
        return "UU" + cid[2:]
    return cid


async def fetch_finance_videos(hours_back: int = 24) -> list[dict]:
    """
    Fetch videos published in the last `hours_back` hours from all FINANCE_CHANNELS.
    Returns list of dicts: {channel, title, description, published, tags, video_id}.
    Returns empty list if no YouTube-capable API key is set.
    """
    if not youtube_api_key_candidates():
        logger.info("[YouTubeConnector] No YouTube API key — skipping YouTube ingestion.")
        return []
    return await asyncio.to_thread(_sync_fetch_all, hours_back)


def _sync_fetch_all(hours_back: int) -> list[dict]:
    from .fetch_utils import request_with_backoff

    since_dt = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    since_iso = since_dt.isoformat().replace("+00:00", "Z")
    keys = youtube_api_key_candidates()

    for key_idx, api_key in enumerate(keys):
        all_videos: List[dict] = []
        failed_channels: List[str] = []

        for channel in FINANCE_CHANNELS:
            try:
                videos = _fetch_channel_videos_playlist(
                    request_with_backoff,
                    channel,
                    since_dt,
                    api_key=api_key,
                )
                all_videos.extend(videos)
            except Exception as e:
                failed_channels.append(channel["name"])
                logger.warning(
                    "[YouTubeConnector] Failed for channel %s (key_idx=%d): %s",
                    channel["name"],
                    key_idx,
                    e,
                )

        if all_videos:
            logger.info(
                "[YouTubeConnector] Ingested %d videos (key_idx=%d, since=%s).",
                len(all_videos),
                key_idx,
                since_iso,
            )
            return all_videos

        if failed_channels and len(failed_channels) == len(FINANCE_CHANNELS):
            if key_idx < len(keys) - 1:
                logger.warning(
                    "[YouTubeConnector] All channels failed with key_idx=%d — trying fallback key.",
                    key_idx,
                )
                continue
            logger.error(
                "[YouTubeConnector] All %d finance channels failed on every API key.",
                len(FINANCE_CHANNELS),
            )
        elif failed_channels:
            logger.warning(
                "[YouTubeConnector] Partial channel failures (%s); ingested %d videos.",
                ", ".join(failed_channels),
                len(all_videos),
            )
            return all_videos

    return []


def _fetch_channel_videos_playlist(
    http_get,
    channel: dict,
    since_dt: datetime,
    *,
    api_key: str,
) -> list[dict]:
    """Walk the channel uploads playlist (1 quota unit per page)."""
    playlist_id = channel_uploads_playlist_id(channel["id"])
    page_token: Optional[str] = None
    videos: List[dict] = []
    pages = 0
    max_pages = 3  # up to 150 recent uploads scanned per channel

    while pages < max_pages:
        params: Dict[str, Any] = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": _PLAYLIST_PAGE_SIZE,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = http_get(
            "GET",
            f"{YOUTUBE_API_BASE}/playlistItems",
            params=params,
            timeout=12,
        )
        data = resp.json()
        items = data.get("items") or []
        if not items:
            break

        stop_paging = False
        for item in items:
            snippet = item.get("snippet") or {}
            published_raw = snippet.get("publishedAt") or ""
            if not published_raw:
                continue
            try:
                published_dt = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if published_dt < since_dt:
                stop_paging = True
                continue

            video_id = (item.get("contentDetails") or {}).get("videoId") or ""
            if not video_id:
                continue
            title = snippet.get("title") or ""
            description = (snippet.get("description") or "")[:500]
            published = published_raw[:10]
            tags = snippet.get("tags") or []

            videos.append({
                "channel": channel["name"],
                "channel_id": channel["id"],
                "video_id": video_id,
                "title": title,
                "description": description,
                "published": published,
                "tags": tags,
            })

        pages += 1
        if stop_paging:
            break
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return videos
