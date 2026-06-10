import asyncio
import yfinance as yf
from typing import Dict, Any
from ..data_errors import InsufficientDataError
from .base import DataConnector
from ..connector_cache import get_cached, set_cached

class ShortsConnector(DataConnector):
    """
    Fetches real Short Interest Ratio (SIR) and Short Percent of Float using yfinance.
    """
    def __init__(self, force_high_sir: bool = False):
        self.force_high_sir = force_high_sir

    async def fetch_data(self, ticker: str = "GME", **kwargs) -> Dict[str, Any]:
        ticker = kwargs.get("ticker", ticker).upper()
        cached = get_cached("shorts", ticker)
        if cached is not None:
            return cached

        # Run synchronous yfinance request in a threadpool to prevent blocking the async loop
        def get_yf_data():
            t = yf.Ticker(ticker)
            return t.info

        try:
            info = await asyncio.to_thread(get_yf_data)
        except Exception as e:
            raise InsufficientDataError(
                "yfinance",
                f"Live short-interest fetch failed for {ticker}: {e}",
                ticker=ticker,
                missing=["short_interest_ratio", "days_to_cover"],
            ) from e

        # yfinance format: shortPercentOfFloat (e.g. 0.15 for 15%), shortRatio (Days to Cover)
        sir_raw = (info or {}).get("shortPercentOfFloat")
        short_ratio = (info or {}).get("shortRatio")

        if sir_raw is None and short_ratio is None:
            raise InsufficientDataError(
                "yfinance",
                f"Short-interest data is not available for {ticker}; "
                "refusing to report 0% as if it were real.",
                ticker=ticker,
                missing=["short_interest_ratio", "days_to_cover"],
            )

        sir_percent = round((sir_raw * 100), 2) if sir_raw is not None else 0.0
        dtc = round(short_ratio, 2) if short_ratio is not None else 0.0

        result = {
            "source": "yfinance API (Live)",
            "ticker": ticker,
            "short_interest_ratio": sir_percent,
            "days_to_cover": dtc,
            "squeeze_probability": "High" if sir_percent > 15.0 else "Low"
        }
        set_cached("shorts", result, ticker)
        return result
