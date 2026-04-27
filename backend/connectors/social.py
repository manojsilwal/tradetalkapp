import asyncio
import urllib.request
import urllib.parse
import defusedxml.ElementTree as ET
from typing import Dict, Any, List
from .base import DataConnector
from ..connector_cache import get_cached, set_cached

class SocialSentimentConnector(DataConnector):
    """
    Scrapes live YouTube influencer video titles and recent Blog posts
    by parsing the Google News RSS feed.
    """
    async def fetch_data(self, ticker: str = "SPY", **kwargs) -> Dict[str, Any]:
        ticker = kwargs.get("ticker", ticker).upper()
        cached = get_cached("social", ticker)
        if cached is not None:
            return cached

        def fetch_rss_titles(query: str, limit: int = 15) -> List[str]:
            q = urllib.parse.quote(query)
            # Use '1m' age to vaguely target recent (though Google News handles recency inherently based on buzz)
            url = f"https://news.google.com/rss/search?q={q}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            titles = []
            try:
                html = urllib.request.urlopen(req, timeout=5).read()
                root = ET.fromstring(html)
                items = root.findall(".//item")
                for i in items[:limit]:
                    title_elem = i.find("title")
                    if title_elem is not None and title_elem.text:
                        # Clean up title (Google adds publisher at the end)
                        raw_title = title_elem.text
                        clean = raw_title.split(" - ")[0] if " - " in raw_title else raw_title
                        titles.append(clean)
            except Exception as e:
                pass
            return titles

        def get_all_social():
            blogs = fetch_rss_titles(f"{ticker} stock blog", limit=20)
            yt = fetch_rss_titles(f"{ticker} stock site:youtube.com", limit=20)
            return {"blogs": blogs, "youtube": yt}

        try:
            results = await asyncio.to_thread(get_all_social)
        except Exception:
            results = {"blogs": [], "youtube": []}
            
        combined_titles = results["blogs"] + results["youtube"]
        
        result = {
            "source": "Live Google News RSS (Blogs & YouTube)",
            "ticker": ticker,
            "recent_titles": combined_titles,
            "counts": {
                "blogs": len(results["blogs"]),
                "youtube": len(results["youtube"])
            }
        }
        set_cached("social", result, ticker)
        return result
