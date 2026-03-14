import asyncio
import hashlib
import time
import requests
import xml.etree.ElementTree as ET
from typing import Dict, Any, List
from .base import DataConnector

MACRO_KEYWORDS = [
    "CPI report", "CPI data", "consumer price index",
    "Federal Reserve", "Fed decision", "FOMC", "interest rate",
    "GDP report", "GDP data", "gross domestic product",
    "unemployment rate", "jobs report", "nonfarm payroll", "labor market",
    "inflation data", "inflation report", "core inflation",
    "tariff", "trade war", "trade policy", "trade deal",
    "government shutdown", "debt ceiling", "fiscal policy",
    "treasury yield", "bond market", "yield curve",
    "recession", "economic slowdown", "banking crisis",
    "oil prices", "OPEC", "energy crisis",
    "supply chain", "semiconductor shortage",
    "housing market", "real estate market",
    "crypto regulation", "SEC ruling",
    "sanctions", "geopolitical",
]

SEARCH_QUERIES = [
    "CPI report economy",
    "Federal Reserve interest rate decision",
    "GDP report US economy",
    "unemployment rate jobs report",
    "inflation data report",
    "tariff trade war policy",
    "government shutdown debt ceiling",
    "treasury yield bond market",
    "recession economic outlook",
    "OPEC oil prices energy",
    "sector rotation market",
]

class NewsScannerConnector(DataConnector):
    """Polls Google News RSS for macro-level market-moving headlines."""

    def __init__(self):
        self._seen_hashes: set = set()
        self._last_scan: float = 0

    def _hash_title(self, title: str) -> str:
        return hashlib.md5(title.strip().lower().encode()).hexdigest()

    def _is_macro_relevant(self, title: str, snippet: str) -> bool:
        combined = (title + " " + snippet).lower()
        return any(kw.lower() in combined for kw in MACRO_KEYWORDS)

    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        def scan_rss():
            all_items = []
            for query in SEARCH_QUERIES[:5]:
                try:
                    url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
                    resp = requests.get(url, timeout=5)
                    resp.raise_for_status()
                    root = ET.fromstring(resp.content)
                    channel = root.find("channel")
                    if channel is None:
                        continue
                    for item in channel.findall("item")[:5]:
                        title = item.findtext("title", "")
                        link = item.findtext("link", "")
                        pub_date = item.findtext("pubDate", "")
                        source = item.findtext("source", "Unknown")
                        description = item.findtext("description", "")
                        all_items.append({
                            "title": title, "link": link, "pub_date": pub_date,
                            "source": source, "snippet": description[:300],
                        })
                except Exception:
                    continue
            return all_items

        raw_items = await asyncio.to_thread(scan_rss)
        new_items = []
        for item in raw_items:
            h = self._hash_title(item["title"])
            if h in self._seen_hashes:
                continue
            if not self._is_macro_relevant(item["title"], item["snippet"]):
                continue
            self._seen_hashes.add(h)
            new_items.append(item)

        self._last_scan = time.time()
        return {
            "source": "Google News RSS (Live)",
            "new_headlines": new_items,
            "total_scanned": len(raw_items),
            "total_new": len(new_items),
        }

    def _sync_fetch(self) -> Dict[str, Any]:
        """Synchronous version of fetch_data for use inside run_in_executor."""
        all_items = []
        for query in SEARCH_QUERIES[:5]:
            try:
                url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
                channel = root.find("channel")
                if channel is None:
                    continue
                for item in channel.findall("item")[:5]:
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    pub_date = item.findtext("pubDate", "")
                    source = item.findtext("source", "Unknown")
                    description = item.findtext("description", "")
                    all_items.append({
                        "title": title, "link": link, "pub_date": pub_date,
                        "source": source, "snippet": description[:300],
                    })
            except Exception:
                continue

        new_items = []
        for item in all_items:
            h = self._hash_title(item["title"])
            if h in self._seen_hashes:
                continue
            if not self._is_macro_relevant(item["title"], item["snippet"]):
                continue
            self._seen_hashes.add(h)
            new_items.append(item)

        self._last_scan = time.time()
        return {
            "source": "Google News RSS (Live)",
            "new_headlines": new_items,
            "total_scanned": len(all_items),
            "total_new": len(new_items),
        }
