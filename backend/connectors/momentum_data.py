"""
Fetch OHLCV inputs for the composite momentum model.

Loads stock + SPY + sector ETF history via yfinance. Benchmark series are
cached in-process (TTL) since they are shared across all tickers.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from backend.data_errors import InsufficientDataError

logger = logging.getLogger(__name__)

SECTOR_ETF_MAP: Dict[str, str] = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Financial": "XLF",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Energy": "XLE",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Industrials": "XLI",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

_BENCHMARK_CACHE: Dict[str, Tuple[float, pd.DataFrame]] = {}
_CACHE_TTL_SEC = 300.0


def sector_etf_for(sector: Optional[str]) -> str:
    if not sector:
        return "SPY"
    return SECTOR_ETF_MAP.get(sector.strip(), "SPY")


def _fetch_history_sync(symbol: str, period: str = "1y") -> pd.DataFrame:
    import yfinance as yf

    t = yf.Ticker(symbol.upper())
    hist = t.history(period=period)
    if hist is None or hist.empty:
        time.sleep(1.0)
        hist = t.history(period=period)
    if hist is None or hist.empty:
        return pd.DataFrame()
    return hist


def _cached_benchmark(symbol: str) -> pd.DataFrame:
    now = time.time()
    cached = _BENCHMARK_CACHE.get(symbol.upper())
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1].copy()
    df = _fetch_history_sync(symbol, "1y")
    if not df.empty:
        _BENCHMARK_CACHE[symbol.upper()] = (now, df.copy())
    return df


def clear_benchmark_cache_for_tests() -> None:
    _BENCHMARK_CACHE.clear()


def fetch_momentum_inputs_sync(
    ticker: str,
    info: Optional[Dict[str, Any]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """
    Returns (stock_df, spy_df, sector_df, metadata).
    Raises InsufficientDataError when stock history is unusable.
    """
    t_up = ticker.upper().strip()
    info = info or {}
    try:
        import yfinance as yf

        stock_t = yf.Ticker(t_up)
        if not info:
            info = stock_t.info or {}

        stock_df = _fetch_history_sync(t_up, "1y")
        if stock_df.empty or "Close" not in stock_df.columns:
            raise InsufficientDataError(
                "yfinance",
                f"No usable 1-year price history for {t_up}.",
                ticker=t_up,
                missing=["price_history_1y"],
            )

        last = float(stock_df["Close"].iloc[-1])
        if last <= 0 or pd.isna(last):
            raise InsufficientDataError(
                "yfinance",
                f"Invalid close price for {t_up}.",
                ticker=t_up,
                missing=["price_history_1y"],
            )

        spy_df = _cached_benchmark("SPY")
        if spy_df.empty:
            raise InsufficientDataError(
                "yfinance",
                "SPY benchmark history unavailable for momentum model.",
                ticker=t_up,
                missing=["spy_history_1y"],
            )

        sector = info.get("sector") or "Unknown"
        etf_sym = sector_etf_for(sector)
        sector_df = _cached_benchmark(etf_sym) if etf_sym != "SPY" else spy_df
        if sector_df.empty:
            sector_df = spy_df

        metadata = {
            "ticker": t_up,
            "sector": sector,
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta", 1.0),
            "sector_etf": etf_sym,
        }
        return stock_df, spy_df, sector_df, metadata

    except InsufficientDataError:
        raise
    except Exception as e:
        logger.warning("[momentum_data] fetch failed for %s: %s", t_up, e)
        raise InsufficientDataError(
            "yfinance",
            f"Momentum data fetch failed for {t_up}: {e}",
            ticker=t_up,
            missing=["price_history_1y"],
        ) from e


async def fetch_momentum_inputs(
    ticker: str,
    info: Optional[Dict[str, Any]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    import asyncio

    return await asyncio.to_thread(fetch_momentum_inputs_sync, ticker, info)
