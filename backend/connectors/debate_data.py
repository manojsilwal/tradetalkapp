"""
Debate Data Connector — fetches price momentum data for the AI debate agents.
Uses yFinance: 52-week high/low positioning, 1m/3m returns, beta, short interest.

When Yahoo history is empty (common from blocked datacenter IPs), falls back to
yfinance info/fast_info, then Stooq, then FinCrawler quote scrape.
"""
import asyncio
import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

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
    - spot_price_source: yfinance_history | yfinance_info | stooq | fincrawler | none
    - market_data_degraded: True when history-based momentum may be incomplete
    """
    return await asyncio.to_thread(_sync_fetch, ticker)


def _spot_from_info(info: Dict[str, Any]) -> Optional[float]:
    if not info:
        return None
    for key in ("currentPrice", "regularMarketPrice", "postMarketPrice", "preMarketPrice"):
        v = info.get(key)
        if v is None:
            continue
        try:
            f = float(v)
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return None


def _spot_from_fast_info(fi: Any) -> Optional[float]:
    if fi is None:
        return None
    keys = ("last_price", "lastPrice", "regularMarketPrice")
    if isinstance(fi, dict):
        for k in keys:
            v = fi.get(k)
            if v is None:
                continue
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                continue
        return None
    for k in keys:
        v = getattr(fi, k, None)
        if v is None:
            continue
        try:
            f = float(v)
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return None


def _build_record_from_history(
    ticker: str,
    info: Dict[str, Any],
    prices,
    *,
    spot_source: str,
    degraded: bool,
) -> Dict[str, Any]:
    current_price = float(prices.iloc[-1])

    def pct_return(periods: int) -> float:
        if len(prices) < periods + 1:
            return 0.0
        return float((prices.iloc[-1] / prices.iloc[-periods - 1] - 1) * 100)

    return_1m = pct_return(21)
    return_3m = pct_return(63)
    return_6m = pct_return(min(126, len(prices) - 1))

    week_52_high = float(info.get("fiftyTwoWeekHigh") or prices.max())
    week_52_low = float(info.get("fiftyTwoWeekLow") or prices.min())
    pct_of_52wk_high = (current_price / week_52_high) if week_52_high else 0.0
    pct_of_52wk_low = ((current_price - week_52_low) / week_52_low) if week_52_low else 0.0

    return _enrich_fundamentals(
        ticker,
        info,
        {
            "current_price": round(current_price, 2),
            "price_return_1m": round(return_1m, 2),
            "price_return_3m": round(return_3m, 2),
            "price_return_6m": round(return_6m, 2),
            "pct_of_52wk_high": round(pct_of_52wk_high, 3),
            "pct_of_52wk_low": round(pct_of_52wk_low, 3),
            "week_52_high": round(week_52_high, 2),
            "week_52_low": round(week_52_low, 2),
            "spot_price_source": spot_source,
            "market_data_degraded": degraded,
        },
    )


def _enrich_fundamentals(ticker: str, info: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    base.update(
        {
            "ticker": ticker.upper(),
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
    )
    return base


def _build_spot_only_record(
    ticker: str,
    info: Dict[str, Any],
    spot: float,
    *,
    spot_source: str,
) -> Dict[str, Any]:
    wh = info.get("fiftyTwoWeekHigh")
    wl = info.get("fiftyTwoWeekLow")
    week_52_high = float(wh) if wh is not None else round(spot * 1.12, 2)
    week_52_low = float(wl) if wl is not None else round(spot * 0.88, 2)
    pct_of_52wk_high = (spot / week_52_high) if week_52_high else 0.5
    pct_of_52wk_low = ((spot - week_52_low) / week_52_low) if week_52_low else 0.5

    base = {
        "current_price": round(spot, 2),
        "price_return_1m": 0.0,
        "price_return_3m": 0.0,
        "price_return_6m": 0.0,
        "pct_of_52wk_high": round(pct_of_52wk_high, 3),
        "pct_of_52wk_low": round(pct_of_52wk_low, 3),
        "week_52_high": round(week_52_high, 2),
        "week_52_low": round(week_52_low, 2),
        "spot_price_source": spot_source,
        "market_data_degraded": True,
    }
    return _enrich_fundamentals(ticker.upper(), info, base)


def _sync_fetch(ticker: str) -> dict:
    from backend.connectors.quote_fallbacks import fetch_us_equity_spot

    t_up = ticker.upper().strip()
    try:
        import yfinance as yf

        t = yf.Ticker(t_up)
        info = t.info or {}

        hist_6m = t.history(period="6mo")
        if hist_6m.empty:
            time.sleep(1.5)
            hist_6m = t.history(period="6mo")

        if not hist_6m.empty:
            prices = hist_6m["Close"]
            try:
                last = float(prices.iloc[-1])
            except (TypeError, ValueError):
                last = 0.0
            if not math.isnan(last) and last > 0:
                return _build_record_from_history(
                    t_up,
                    info,
                    prices,
                    spot_source="yfinance_history",
                    degraded=False,
                )

        spot: Optional[float] = None
        spot_src = "none"

        spot = _spot_from_info(info)
        if spot:
            spot_src = "yfinance_info"
        if not spot:
            try:
                spot = _spot_from_fast_info(t.fast_info)
                if spot:
                    spot_src = "yfinance_info"
            except Exception:
                pass

        if not spot:
            fb: Optional[Tuple[float, str]] = fetch_us_equity_spot(t_up)
            if fb:
                spot, label = fb
                spot_src = label

        if spot and spot > 0:
            return _build_spot_only_record(t_up, info, spot, spot_source=spot_src)

        logger.warning("[DebateDataConnector] No valid spot for %s — returning empty shell", t_up)
        return _empty_data(t_up)
    except Exception as e:
        logger.warning("[DebateDataConnector] Failed for %s: %s", t_up, e)
        # yfinance can throw or return unusable data from blocked IPs; still try Stooq / FinCrawler.
        try:
            fb: Optional[Tuple[float, str]] = fetch_us_equity_spot(t_up)
            if fb:
                spot, label = fb
                return _build_spot_only_record(t_up, {}, spot, spot_source=label)
        except Exception as e2:
            logger.warning(
                "[DebateDataConnector] Spot fallback after yfinance error failed for %s: %s",
                t_up,
                e2,
            )
        return _empty_data(t_up)


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
        "spot_price_source": None,
        "market_data_degraded": True,
    }
