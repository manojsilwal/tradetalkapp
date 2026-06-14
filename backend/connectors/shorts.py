import asyncio
import logging
import yfinance as yf
from typing import Dict, Any
from ..data_errors import InsufficientDataError
from .base import DataConnector
from ..connector_cache import get_cached, set_cached

logger = logging.getLogger(__name__)

class ShortsConnector(DataConnector):
    """
    Fetches real Short Interest Ratio (SIR) and Short Percent of Float using yfinance,
    with a robust web-scraping fallback to StockAnalysis.com if yfinance is blocked/fails.
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

        info = None
        try:
            info = await asyncio.to_thread(get_yf_data)
        except Exception as e:
            logger.warning("yfinance live short-interest fetch failed for %s: %s", ticker, e)

        # yfinance format: shortPercentOfFloat (e.g. 0.15 for 15%), shortRatio (Days to Cover)
        sir_raw = (info or {}).get("shortPercentOfFloat")
        short_ratio = (info or {}).get("shortRatio")

        used_fallback = False
        if sir_raw is None and short_ratio is None:
            logger.info("Attempting fallback scrape of stockanalysis.com for short interest of %s", ticker)
            try:
                def scrape_stockanalysis():
                    import urllib.request
                    from bs4 import BeautifulSoup
                    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/statistics/"
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"},
                        method="GET"
                    )
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        html = resp.read().decode("utf-8")
                    soup = BeautifulSoup(html, "html.parser")
                    sir = None
                    sr = None
                    for td in soup.find_all("td"):
                        text = td.get_text(strip=True).lower()
                        if "short % of float" in text:
                            sibling = td.find_next_sibling("td")
                            if sibling:
                                raw_val = sibling.get_text(strip=True).replace("%", "")
                                try:
                                    sir = float(raw_val)
                                except ValueError:
                                    pass
                        elif "short ratio" in text:
                            sibling = td.find_next_sibling("td")
                            if sibling:
                                raw_val = sibling.get_text(strip=True)
                                try:
                                    sr = float(raw_val)
                                except ValueError:
                                    pass
                    return sir, sr

                sir_raw_fb, short_ratio_fb = await asyncio.to_thread(scrape_stockanalysis)
                if sir_raw_fb is not None or short_ratio_fb is not None:
                    if sir_raw_fb is not None:
                        sir_raw = sir_raw_fb / 100.0
                    if short_ratio_fb is not None:
                        short_ratio = short_ratio_fb
                    used_fallback = True
                    logger.info("Successfully retrieved short interest from StockAnalysis for %s: sir=%s%%, sr=%s", ticker, sir_raw_fb, short_ratio)
            except Exception as fb_exc:
                logger.warning("StockAnalysis fallback scrape failed for %s: %s", ticker, fb_exc)

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
            "source": "StockAnalysis Fallback Scraper" if used_fallback else "yfinance API (Live)",
            "ticker": ticker,
            "short_interest_ratio": sir_percent,
            "days_to_cover": dtc,
            "squeeze_probability": "High" if sir_percent > 15.0 else "Low"
        }
        set_cached("shorts", result, ticker)
        return result
