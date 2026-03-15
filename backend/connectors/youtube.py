"""
YouTube Finance Connector — fetches latest videos from top finance channels.
Requires YOUTUBE_API_KEY (YouTube Data API v3, free: 10,000 units/day).
Gracefully skips if no API key is configured.
"""
import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

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


async def fetch_finance_videos(hours_back: int = 24) -> list[dict]:
    """
    Fetch videos published in the last `hours_back` hours from all FINANCE_CHANNELS.
    Returns list of dicts: {channel, title, description, published, tags, video_id}.
    Returns empty list if YOUTUBE_API_KEY is not set.
    """
    if not YOUTUBE_API_KEY:
        logger.info("[YouTubeConnector] YOUTUBE_API_KEY not set — skipping YouTube ingestion.")
        return []
    return await asyncio.to_thread(_sync_fetch_all, hours_back)


def _sync_fetch_all(hours_back: int) -> list[dict]:
    import requests
    since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat().replace("+00:00", "Z")
    all_videos = []
    for channel in FINANCE_CHANNELS:
        try:
            videos = _fetch_channel_videos(requests, channel, since)
            all_videos.extend(videos)
        except Exception as e:
            logger.warning(f"[YouTubeConnector] Failed for channel {channel['name']}: {e}")
    return all_videos


def _fetch_channel_videos(requests, channel: dict, published_after: str) -> list[dict]:
    params = {
        "part": "snippet",
        "channelId": channel["id"],
        "publishedAfter": published_after,
        "order": "date",
        "type": "video",
        "maxResults": 10,
        "key": YOUTUBE_API_KEY,
    }
    resp = requests.get(f"{YOUTUBE_API_BASE}/search", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    videos = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId", "")
        title = snippet.get("title", "")
        description = snippet.get("description", "")[:500]
        published = snippet.get("publishedAt", "")[:10]
        tags = snippet.get("tags", [])

        videos.append({
            "channel": channel["name"],
            "channel_id": channel["id"],
            "video_id": video_id,
            "title": title,
            "description": description,
            "published": published,
            "tags": tags,
        })
    return videos
