"""
Data layer for the Narrative Rotation Radar (Plan NR-2).

Unified OHLCV fetch via ``yfinance_batch.history_by_ticker`` (6mo daily bars).
Closes are derived from the ``Close`` column; volume/high/low feed accumulation
signals (smart-money CMF + relative-volume z-score).

SPY is fetched the same way and used as the relative-strength benchmark.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from ..picks_shovels import data as ps_data

logger = logging.getLogger(__name__)

BENCHMARK = "SPY"
OHLCV_PERIOD = "6mo"


def fetch_ohlcv(tickers: Sequence[str]) -> Dict[str, pd.DataFrame]:
    """Batched OHLCV history per ticker (rate-limit-safe chunking)."""
    from ..connectors import yfinance_batch

    if not tickers:
        return {}
    return yfinance_batch.history_by_ticker(
        list(tickers),
        period=OHLCV_PERIOD,
        interval="1d",
        chunk_size=max(1, len(tickers)),
    )


def fetch_benchmark_ohlcv() -> pd.DataFrame:
    series = fetch_ohlcv([BENCHMARK])
    return series.get(BENCHMARK, pd.DataFrame())


def fetch_benchmark_closes() -> List[float]:
    df = fetch_benchmark_ohlcv()
    return _closes_from_df(df)


def _closes_from_df(df: Optional[pd.DataFrame]) -> List[float]:
    if df is None or df.empty or "Close" not in df.columns:
        return []
    return [float(v) for v in df["Close"].dropna().tolist()]


def fetch_closes(tickers: Sequence[str]) -> Dict[str, List[float]]:
    """Backward-compatible close-series fetch (derived from OHLCV)."""
    ohlcv = fetch_ohlcv(tickers)
    return {sym: _closes_from_df(df) for sym, df in ohlcv.items()}


def fetch_market_cap(ticker: str) -> Dict[str, Any]:
    return ps_data.fetch_fundamentals_extended(ticker)


def momentum_from_closes(closes: Sequence[float]) -> Dict[str, Any]:
    return ps_data.momentum_from_closes(closes)


def build_member_row(
    ticker: str,
    ohlcv: Optional[pd.DataFrame],
    fundamentals: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble per-member input for ``features.build_theme_features``."""
    closes = _closes_from_df(ohlcv)
    row: Dict[str, Any] = {
        "ticker": ticker.upper(),
        "closes": closes,
        "ohlcv": ohlcv if ohlcv is not None and not ohlcv.empty else None,
        "momentum": momentum_from_closes(closes),
        "fundamentals": fundamentals or {},
    }
    return row
