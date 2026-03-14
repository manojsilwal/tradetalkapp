import asyncio
import yfinance as yf
from typing import Dict, Any
from .base import DataConnector

class ShortsConnector(DataConnector):
    """
    Fetches real Short Interest Ratio (SIR) and Short Percent of Float using yfinance.
    """
    def __init__(self, force_high_sir: bool = False):
        self.force_high_sir = force_high_sir

    async def fetch_data(self, ticker: str = "GME", **kwargs) -> Dict[str, Any]:
        # Run synchronous yfinance request in a threadpool to prevent blocking the async loop
        def get_yf_data():
            t = yf.Ticker(ticker)
            return t.info

        try:
            info = await asyncio.to_thread(get_yf_data)
        except Exception as e:
            # Fallback if yfinance rate limits or fails
            info = {}

        # yfinance format: shortPercentOfFloat (e.g. 0.15 for 15%), shortRatio (Days to Cover)
        sir_raw = info.get("shortPercentOfFloat")
        short_ratio = info.get("shortRatio")
        
        # Parse to percentages, default to 0 if missing
        sir_percent = round((sir_raw * 100), 2) if sir_raw is not None else 0.0
        dtc = round(short_ratio, 2) if short_ratio is not None else 0.0

        return {
            "source": "yfinance API (Live)",
            "ticker": ticker,
            "short_interest_ratio": sir_percent,
            "days_to_cover": dtc,
            "squeeze_probability": "High" if sir_percent > 15.0 else "Low"
        }
