"""
Chunked yfinance batch downloads with backoff between chunks.

Strategy: one ``yf.download`` call covers many tickers (Yahoo batches internally).
Split large universes into chunks (default 50) and pause between chunks so free
API rate limits are less likely to trip — parallel fan-out of *separate* calls
does **not** raise quotas and usually makes 429s worse.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Union

import pandas as pd

from .fetch_utils import sleep_backoff

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = int(os.environ.get("YFINANCE_BATCH_CHUNK_SIZE", "50") or "50")
DEFAULT_MAX_RETRIES = int(os.environ.get("YFINANCE_BATCH_MAX_RETRIES", "3") or "3")
INTER_CHUNK_DELAY_S = float(os.environ.get("YFINANCE_BATCH_INTER_CHUNK_DELAY_S", "0.35") or "0.35")


def chunk_tickers(tickers: Sequence[str], chunk_size: int = DEFAULT_CHUNK_SIZE) -> List[List[str]]:
    """Split a ticker list into fixed-size chunks (deduped, order preserved)."""
    seen: set[str] = set()
    ordered: List[str] = []
    for raw in tickers:
        sym = (raw or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    size = max(1, chunk_size)
    return [ordered[i : i + size] for i in range(0, len(ordered), size)]


def _download_chunk(
    tickers: Sequence[str],
    *,
    max_retries: int,
    download_kwargs: Mapping[str, object],
) -> pd.DataFrame:
    import yfinance as yf

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            raw = yf.download(
                list(tickers),
                group_by="ticker",
                threads=True,
                **download_kwargs,
            )
            return raw if isinstance(raw, pd.DataFrame) else pd.DataFrame()
        except Exception as exc:
            last_err = exc
            logger.warning(
                "[yfinance_batch] download failed (attempt %s/%s, n=%s): %s",
                attempt + 1,
                max_retries,
                len(tickers),
                exc,
            )
            if attempt < max_retries - 1:
                sleep_backoff(attempt)
    if last_err is not None:
        raise last_err
    return pd.DataFrame()


def download_history(
    tickers: Union[str, Sequence[str]],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
    inter_chunk_delay: float = INTER_CHUNK_DELAY_S,
    **download_kwargs: object,
) -> pd.DataFrame:
    """
    Download OHLCV for many tickers, merging chunk DataFrames.

    For a single ticker returns a plain OHLCV frame. For multiple tickers
    returns the multi-index column layout from yfinance (group_by=ticker).
    """
    if isinstance(tickers, str):
        sym = tickers.strip().upper()
        return _download_chunk([sym], max_retries=max_retries, download_kwargs=download_kwargs)

    chunks = chunk_tickers(list(tickers), chunk_size)
    if not chunks:
        return pd.DataFrame()
    if len(chunks) == 1:
        return _download_chunk(chunks[0], max_retries=max_retries, download_kwargs=download_kwargs)

    parts: List[pd.DataFrame] = []
    for idx, chunk in enumerate(chunks):
        parts.append(_download_chunk(chunk, max_retries=max_retries, download_kwargs=download_kwargs))
        if idx < len(chunks) - 1 and inter_chunk_delay > 0:
            time.sleep(inter_chunk_delay)

    # Concatenate along ticker columns when all parts are multi-ticker frames.
    non_empty = [p for p in parts if not p.empty]
    if not non_empty:
        return pd.DataFrame()
    if len(non_empty) == 1:
        return non_empty[0]
    try:
        return pd.concat(non_empty, axis=1)
    except Exception:
        return non_empty[0]


def close_series_by_ticker(
    raw: pd.DataFrame,
    tickers: Sequence[str],
) -> Dict[str, pd.Series]:
    """Extract ``Close`` series keyed by canonical ticker symbol."""
    out: Dict[str, pd.Series] = {}
    if raw.empty:
        return out

    ticker_list = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
    if not ticker_list:
        return out

    if len(ticker_list) == 1:
        sym = ticker_list[0]
        if "Close" in raw.columns:
            out[sym] = raw["Close"].dropna()
        return out

    if isinstance(raw.columns, pd.MultiIndex):
        for sym in ticker_list:
            try:
                if sym in raw.columns.get_level_values(0):
                    out[sym] = raw[sym]["Close"].dropna()
            except (KeyError, TypeError):
                continue
    elif "Close" in raw.columns:
        # Single-ticker frame returned despite multi request
        out[ticker_list[0]] = raw["Close"].dropna()
    return out


def daily_change_pct_from_close(close: pd.Series) -> Optional[float]:
    """Last trading day's % change from the prior close."""
    clean = close.dropna()
    if len(clean) < 2:
        return None
    prev = float(clean.iloc[-2])
    curr = float(clean.iloc[-1])
    if prev == 0:
        return None
    return round(((curr - prev) / prev) * 100.0, 2)


def batch_daily_change_pct(
    tickers: Sequence[str],
    *,
    period: str = "5d",
    interval: str = "1d",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Dict[str, float]:
    """
    One (or few) batched ``yf.download`` calls → daily % change per ticker.

    Falls back to an empty dict on total failure; callers should use
    ``quote_fallbacks.yahoo_chart_change_pct`` per missing symbol.
    """
    syms = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
    if not syms:
        return {}

    try:
        raw = download_history(
            syms,
            chunk_size=chunk_size,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        logger.warning("[yfinance_batch] batch_daily_change_pct failed: %s", exc)
        return {}

    result: Dict[str, float] = {}
    for sym, close in close_series_by_ticker(raw, syms).items():
        pct = daily_change_pct_from_close(close)
        if pct is not None:
            result[sym] = pct
    return result


def history_by_ticker(
    tickers: Sequence[str],
    *,
    period: str = "5y",
    interval: str = "1d",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Dict[str, pd.DataFrame]:
    """Batch download full OHLCV history per ticker (for capital-flow charts)."""
    syms = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
    if not syms:
        return {}

    try:
        raw = download_history(
            syms,
            chunk_size=chunk_size,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        logger.warning("[yfinance_batch] history_by_ticker failed: %s", exc)
        return {}

    out: Dict[str, pd.DataFrame] = {}
    if len(syms) == 1:
        sym = syms[0]
        if not raw.empty:
            out[sym] = raw.dropna(how="all")
        return out

    if isinstance(raw.columns, pd.MultiIndex):
        for sym in syms:
            try:
                if sym in raw.columns.get_level_values(0):
                    df = raw[sym].dropna(how="all")
                    if not df.empty:
                        out[sym] = df
            except (KeyError, TypeError):
                continue
    return out
