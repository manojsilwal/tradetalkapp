"""Batch OHLCV fetch for macro_flow (yfinance), keyed by UI interval."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# UI interval -> (yfinance period, yfinance interval)
_INTERVAL_MAP: Dict[str, Tuple[str, str]] = {
    "1d": ("1mo", "1d"),
    "1w": ("3mo", "1d"),
    "1m": ("1y", "1d"),
    "1y": ("5y", "1wk"),
}


def yf_period_interval(ui_interval: str) -> Tuple[str, str]:
    return _INTERVAL_MAP.get(ui_interval.strip().lower(), ("3mo", "1d"))


def _download_one(ticker: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df["Ticker"] = ticker
    return df


async def fetch_ohlcv_batch(
    tickers: List[str],
    ui_interval: str,
) -> Dict[str, pd.DataFrame]:
    """Return mapping ticker -> OHLCV DataFrame (may be empty on failure)."""
    period, interval = yf_period_interval(ui_interval)
    uniq = sorted({t.upper().strip() for t in tickers if t})

    def _all() -> Dict[str, pd.DataFrame]:
        out: Dict[str, pd.DataFrame] = {}
        for sym in uniq:
            try:
                out[sym] = _download_one(sym, period, interval)
            except Exception as e:
                logger.warning("[macro_flow] yfinance %s failed: %s", sym, e)
                out[sym] = pd.DataFrame()
        return out

    return await asyncio.to_thread(_all)
