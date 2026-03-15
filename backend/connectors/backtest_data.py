"""
Backtest Data Connector — fetches historical OHLC price data and available
fundamental snapshots for a list of tickers over a date range using yFinance.
Batches requests with asyncio.to_thread to avoid blocking the event loop.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Curated liquid S&P 500 universe — large enough to be meaningful,
# small enough to avoid yFinance rate limits
SP500_UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "JNJ", "V",
    "PG", "UNH", "HD", "MA", "BAC", "ABBV", "PFE", "AVGO", "KO", "PEP",
    "COST", "MRK", "TMO", "WMT", "CSCO", "ABT", "ACN", "CVX", "LLY", "MCD",
    "DHR", "NEE", "NKE", "TXN", "AMD", "PM", "ORCL", "IBM", "CRM", "QCOM",
    "HON", "AMGN", "LIN", "SBUX", "INTU", "GS", "BLK", "SPGI", "CAT", "BA",
    "AXP", "MS", "RTX", "ISRG", "ADI", "MDLZ", "GILD", "TJX", "BKNG", "NOW",
    "DE", "MMM", "SYK", "ZTS", "CI", "USB", "MO", "REGN", "VRTX", "HCA",
    "EOG", "SLB", "PSA", "WELL", "DUK", "SO", "EXC", "D", "AEP", "XEL",
    "APD", "SHW", "PPG", "ECL", "EMR", "ITW", "GE", "ETN", "PH", "ROK",
    "F", "GM", "UBER", "LYFT", "ABNB", "DASH", "SNAP", "PINS", "TWTR", "ZM",
    "PYPL", "SQ", "SHOP", "ROKU", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
    "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "AVB", "EQR", "MAA", "UDR",
    "XOM", "CVX", "COP", "MPC", "PSX", "VLO", "OXY", "HES", "DVN", "FANG",
    "WFC", "C", "PNC", "TFC", "STT", "BK", "COF", "AIG", "MET", "PRU",
]


async def fetch_backtest_data(tickers: list, start: str, end: str) -> dict:
    """
    Fetch historical price data and fundamentals for all tickers.
    Returns: {ticker: {prices: list of {date, open, high, low, close, volume},
                       annual_financials: dict, info: dict}}
    Batches in groups of 20 to avoid rate limits.
    """
    batch_size = 20
    results = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i: i + batch_size]
        tasks = [asyncio.to_thread(_fetch_one, t, start, end) for t in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for ticker, result in zip(batch, batch_results):
            if isinstance(result, Exception):
                logger.warning(f"[BacktestData] Failed for {ticker}: {result}")
                results[ticker] = {"prices": [], "annual_financials": {}, "info": {}}
            else:
                results[ticker] = result
        if i + batch_size < len(tickers):
            await asyncio.sleep(0.5)  # gentle rate-limit pause
    return results


def _fetch_one(ticker: str, start: str, end: str) -> dict:
    try:
        import yfinance as yf
        import pandas as pd

        t = yf.Ticker(ticker.upper())
        hist = t.history(start=start, end=end, auto_adjust=True)

        prices = []
        if not hist.empty:
            for date_idx, row in hist.iterrows():
                prices.append({
                    "date": str(date_idx.date()),
                    "open": round(float(row["Open"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                    "close": round(float(row["Close"]), 4),
                    "volume": int(row["Volume"]),
                })

        info = t.info or {}

        # Annual financials — only what yFinance reliably provides
        annual_financials = {}
        try:
            fin = t.financials  # income statement (columns = years)
            if fin is not None and not fin.empty:
                for col in fin.columns[:4]:  # last 4 years
                    year_str = str(col.year) if hasattr(col, "year") else str(col)[:4]
                    annual_financials[year_str] = {
                        "total_revenue": _safe_float(fin.get("Total Revenue", {}).get(col)),
                        "net_income": _safe_float(fin.get("Net Income", {}).get(col)),
                        "gross_profit": _safe_float(fin.get("Gross Profit", {}).get(col)),
                    }
        except Exception:
            pass

        # Balance sheet for debt data
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                for col in bs.columns[:4]:
                    year_str = str(col.year) if hasattr(col, "year") else str(col)[:4]
                    if year_str not in annual_financials:
                        annual_financials[year_str] = {}
                    annual_financials[year_str]["total_debt"] = _safe_float(
                        bs.get("Total Debt", bs.get("Long Term Debt", {})).get(col)
                    )
                    annual_financials[year_str]["cash"] = _safe_float(
                        bs.get("Cash And Cash Equivalents", {}).get(col)
                    )
        except Exception:
            pass

        return {"prices": prices, "annual_financials": annual_financials, "info": info}
    except Exception as e:
        logger.warning(f"[BacktestData] Error fetching {ticker}: {e}")
        return {"prices": [], "annual_financials": {}, "info": {}}


def _safe_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        import math
        f = float(val)
        return None if math.isnan(f) else round(f, 2)
    except Exception:
        return None


def resolve_universe(universe_hint: str, tickers: list = None) -> list:
    """
    Resolve universe hint to a list of tickers.
    If specific tickers provided (e.g. ["TSLA", "AAPL"]) use those.
    Otherwise return the curated S&P 500 universe.
    """
    if tickers and len(tickers) > 0:
        return [t.upper() for t in tickers]
    return SP500_UNIVERSE
