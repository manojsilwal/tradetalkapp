import asyncio
import requests
import json
import yfinance as yf
from typing import Dict, Any
from .base import DataConnector
from ..connector_cache import get_cached, set_cached

# Map common tickers to company names / keywords for Polymarket search
TICKER_KEYWORDS = {
    "AAPL": ["Apple", "iPhone"],
    "MSFT": ["Microsoft"],
    "GOOG": ["Google", "Alphabet"],
    "GOOGL": ["Google", "Alphabet"],
    "AMZN": ["Amazon"],
    "META": ["Meta", "Facebook"],
    "TSLA": ["Tesla", "Elon Musk"],
    "NVDA": ["Nvidia"],
    "GME": ["GameStop"],
    "AMC": ["AMC"],
    "MSTR": ["MicroStrategy"],
}

class PolymarketConnector(DataConnector):
    """
    Connects to Polymarket Gamma API to find prediction markets
    relevant to the searched ticker. Falls back to yfinance for
    the company name when the ticker isn't in our static map.
    """
    async def fetch_data(self, ticker: str = "GME", **kwargs) -> Dict[str, Any]:
        ticker_upper = ticker.upper()
        cached = get_cached("polymarket", ticker_upper)
        if cached is not None:
            return cached

        # 1. Resolve keywords: static map first, then yfinance fallback
        keywords = TICKER_KEYWORDS.get(ticker_upper)
        keyword_resolution = "static_map"
        if not keywords:
            keyword_resolution = "yfinance"
            try:
                info = await asyncio.to_thread(lambda: yf.Ticker(ticker_upper).info)
                company_name = info.get("shortName") or info.get("longName") or ""
                # Take the first word of the company name (e.g. "Apple Inc." -> "Apple")
                first_word = company_name.split()[0] if company_name else ""
                keywords = [first_word, ticker_upper] if first_word else [ticker_upper]
            except Exception:
                keywords = [ticker_upper]

        # 2. Fetch a broader set of events from Polymarket
        def get_pm_data():
            try:
                res = requests.get(
                    "https://gamma-api.polymarket.com/events?closed=false&limit=20",
                    timeout=5
                )
                res.raise_for_status()
                return res.json()
            except Exception:
                return []

        events_raw = await asyncio.to_thread(get_pm_data)

        # 3. Filter events that mention any of our keywords (case-insensitive)
        relevant_events = []
        for e in events_raw:
            title = e.get("title", "")
            description = e.get("description", "")
            combined_text = (title + " " + description).lower()

            for kw in keywords:
                if kw.lower() in combined_text:
                    markets = e.get("markets", [])
                    yes_prob = 0.0
                    if markets:
                        prices_str = markets[0].get("outcomePrices")
                        if prices_str:
                            try:
                                prices = json.loads(prices_str)
                                yes_prob = max(float(prices[0]), float(prices[1])) if len(prices) >= 2 else 0.0
                            except (ValueError, IndexError):
                                pass

                    relevant_events.append({
                        "title": title,
                        "probability": round(yes_prob, 2),
                        "volume": e.get("volumeNum", 0)
                    })
                    break  # Don't double-count

        result = {
            "source": "Polymarket Gamma API (Live)",
            "keyword_resolution": keyword_resolution,
            "ticker": ticker_upper,
            "events": relevant_events,
            "has_relevant_data": len(relevant_events) > 0
        }
        set_cached("polymarket", result, ticker_upper)
        return result
