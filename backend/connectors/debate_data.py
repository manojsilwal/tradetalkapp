"""
Debate Data Connector — fetches price momentum data for the AI debate agents.
Uses yFinance: 52-week high/low positioning, 1m/3m returns, beta, short interest.

Truthful-data contract: the debate agents need full 6-month price history to
compute momentum. When Yahoo cannot deliver usable history (common from
blocked datacenter IPs), this connector raises
:class:`backend.data_errors.InsufficientDataError` instead of fabricating
zeroed returns or synthetic 52-week bands.
"""
import asyncio
import logging
import math
import time
from typing import Any, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

from backend.data_errors import InsufficientDataError

from .base import clean_dividend_yield

_DEBATE_DATA_CACHE: Dict[str, Tuple[float, dict]] = {}
_DEBATE_DATA_CACHE_TTL_S = max(30.0, float(__import__("os").environ.get("DEBATE_DATA_CACHE_TTL_S", "120")))


def clear_debate_data_cache(ticker: Optional[str] = None) -> None:
    """Invalidate debate-data cache (e.g. on force-refresh analyze)."""
    if ticker:
        _DEBATE_DATA_CACHE.pop(ticker.upper().strip(), None)
    else:
        _DEBATE_DATA_CACHE.clear()


async def fetch_debate_data(ticker: str) -> dict:
    """
    Returns a dict with:
    - price_return_1m, price_return_3m, price_return_6m
    - pct_of_52wk_high  (e.g. 0.85 = trading at 85% of 52-week high)
    - pct_of_52wk_low   (e.g. 0.40 = trading at 40% above 52-week low)
    - beta
    - short_interest_ratio
    - current_price, market_cap, pe_ratio, pb_ratio, roe, debt_to_equity
    - spot_price_source: yfinance_history
    - market_data_degraded: always False (degraded data raises instead)

    Raises InsufficientDataError when live price history cannot be fetched.
    """
    t_up = ticker.upper().strip()
    now = time.time()
    cached = _DEBATE_DATA_CACHE.get(t_up)
    if cached is not None and (now - cached[0]) < _DEBATE_DATA_CACHE_TTL_S:
        return cached[1]
    result = await asyncio.to_thread(_sync_fetch, t_up)
    _DEBATE_DATA_CACHE[t_up] = (now, result)
    return result


def _build_record_from_history(
    ticker: str,
    info: Dict[str, Any],
    prices,
    *,
    spot_source: str,
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
            "market_data_degraded": False,
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
            "dividend_yield": round(clean_dividend_yield(info.get("dividendYield")), 2),
            "held_percent_institutions": round(
                float(info.get("heldPercentInstitutions") or 0.0) * 100, 2
            ),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "company_name": info.get("longName", ticker.upper()),
        }
    )
    return base


def _sync_fetch(ticker: str) -> dict:
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
                record = _build_record_from_history(
                    t_up,
                    info,
                    prices,
                    spot_source="yfinance_history",
                )
                from .spot import resolve_spot

                spot_q = resolve_spot(t_up, momentum_anchor_usd=last)
                if spot_q is not None:
                    record["current_price"] = round(spot_q.price, 2)
                    record["spot_price_source"] = spot_q.source
                    record["market_data_degraded"] = spot_q.degraded
                    record["momentum_anchor_price"] = round(last, 2)
                return record
    except InsufficientDataError:
        raise
    except Exception as e:
        logger.warning("[DebateDataConnector] yfinance failed for %s: %s", t_up, e)
        raise InsufficientDataError(
            "yfinance",
            f"Live market data fetch failed for {t_up}: {e}",
            ticker=t_up,
            missing=["price_history_6mo"],
        ) from e

    logger.warning("[DebateDataConnector] No usable price history for %s", t_up)
    raise InsufficientDataError(
        "yfinance",
        f"No usable 6-month price history for {t_up}; momentum analysis requires "
        "complete live history. Refusing to substitute placeholder values.",
        ticker=t_up,
        missing=["price_history_6mo"],
    )
