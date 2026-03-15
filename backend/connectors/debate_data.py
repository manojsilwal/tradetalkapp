"""
Debate Data Connector — fetches price momentum data for the AI debate agents.
Uses yFinance: 52-week high/low positioning, 1m/3m returns, beta, short interest.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_debate_data(ticker: str) -> dict:
    """
    Returns a dict with:
    - price_return_1m, price_return_3m, price_return_6m
    - pct_of_52wk_high  (e.g. 0.85 = trading at 85% of 52-week high)
    - pct_of_52wk_low   (e.g. 0.40 = trading at 40% above 52-week low)
    - beta
    - short_interest_ratio
    - current_price, market_cap, pe_ratio, pb_ratio, roe, debt_to_equity
    """
    return await asyncio.to_thread(_sync_fetch, ticker)


def _sync_fetch(ticker: str) -> dict:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
        info = t.info or {}

        hist_6m = t.history(period="6mo")
        if hist_6m.empty:
            return _empty_data(ticker)

        prices = hist_6m["Close"]
        current_price = float(prices.iloc[-1])

        def pct_return(periods: int) -> float:
            if len(prices) < periods + 1:
                return 0.0
            return float((prices.iloc[-1] / prices.iloc[-periods - 1] - 1) * 100)

        # Approximate 1m (~21 trading days), 3m (~63 days), 6m (~126 days)
        return_1m = pct_return(21)
        return_3m = pct_return(63)
        return_6m = pct_return(min(126, len(prices) - 1))

        week_52_high = float(info.get("fiftyTwoWeekHigh") or prices.max())
        week_52_low  = float(info.get("fiftyTwoWeekLow")  or prices.min())
        pct_of_52wk_high = (current_price / week_52_high) if week_52_high else 0.0
        pct_of_52wk_low  = ((current_price - week_52_low) / week_52_low) if week_52_low else 0.0

        return {
            "ticker": ticker.upper(),
            "current_price": round(current_price, 2),
            "price_return_1m": round(return_1m, 2),
            "price_return_3m": round(return_3m, 2),
            "price_return_6m": round(return_6m, 2),
            "pct_of_52wk_high": round(pct_of_52wk_high, 3),
            "pct_of_52wk_low": round(pct_of_52wk_low, 3),
            "week_52_high": round(week_52_high, 2),
            "week_52_low": round(week_52_low, 2),
            "beta": round(float(info.get("beta") or 1.0), 2),
            "short_interest_ratio": round(float(info.get("shortRatio") or 0.0), 2),
            "short_percent_float": round(float(info.get("shortPercentOfFloat") or 0.0) * 100, 2),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "pb_ratio": info.get("priceToBook"),
            "roe": round(float(info.get("returnOnEquity") or 0.0) * 100, 2),
            "debt_to_equity": info.get("debtToEquity"),
            "free_cashflow": info.get("freeCashflow"),
            "revenue_growth": round(float(info.get("revenueGrowth") or 0.0) * 100, 2),
            "gross_margins": round(float(info.get("grossMargins") or 0.0) * 100, 2),
            "dividend_yield": round(float(info.get("dividendYield") or 0.0) * 100, 2),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "company_name": info.get("longName", ticker.upper()),
        }
    except Exception as e:
        logger.warning(f"[DebateDataConnector] Failed for {ticker}: {e}")
        return _empty_data(ticker)


def _empty_data(ticker: str) -> dict:
    return {
        "ticker": ticker.upper(),
        "current_price": 0.0,
        "price_return_1m": 0.0,
        "price_return_3m": 0.0,
        "price_return_6m": 0.0,
        "pct_of_52wk_high": 0.5,
        "pct_of_52wk_low": 0.5,
        "week_52_high": 0.0,
        "week_52_low": 0.0,
        "beta": 1.0,
        "short_interest_ratio": 0.0,
        "short_percent_float": 0.0,
        "market_cap": None,
        "pe_ratio": None,
        "pb_ratio": None,
        "roe": 0.0,
        "debt_to_equity": None,
        "free_cashflow": None,
        "revenue_growth": 0.0,
        "gross_margins": 0.0,
        "dividend_yield": 0.0,
        "sector": "Unknown",
        "industry": "Unknown",
        "company_name": ticker.upper(),
    }
