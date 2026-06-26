"""
Data layer for the Picks & Shovels Momentum Finder.

Reuses the Actionable screener's rate-limit-safe primitives:
  - ``actionable_companies.fetch_chunk_history``  → one batched yfinance download/chunk
  - ``actionable_companies.fetch_fundamentals``   → yfinance ``.info`` snapshot (1h cache)
  - ``actionable_companies.compute_rsi_14``       → RSI helper

The MVP fills price-momentum and fundamentals for real; backlog/RPO and
news/filing evidence are returned as explicit "unavailable" stubs (Phase 3 hooks)
so the scorer keeps them neutral instead of fabricating values (Plan §18).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from ..actionable_companies import (
    compute_rsi_14,
    fetch_chunk_history,
    fetch_fundamentals,
)


def momentum_from_closes(closes: Sequence[float]) -> Dict[str, Optional[float]]:
    """
    Momentum features from ~1y of daily closes (newest last). Extends the
    Actionable variant with a 12M return, 50/200-DMA distance, and 52wk-high %.
    Volume confirmation needs an OHLCV path (not closes-only) → left None for now.
    """
    clean = [float(c) for c in closes if c is not None and not math.isnan(float(c))]
    out: Dict[str, Optional[float]] = {
        "last_close": None,
        "ret_1m_pct": None,
        "ret_3m_pct": None,
        "ret_6m_pct": None,
        "ret_12m_pct": None,
        "pct_of_52wk_high": None,
        "rsi_14": None,
        "above_50dma_pct": None,
        "above_200dma_pct": None,
        "vol_ratio": None,
    }
    if not clean:
        return out
    last = clean[-1]
    out["last_close"] = round(last, 4)

    def _ret(days: int) -> Optional[float]:
        if len(clean) <= days:
            return None
        base = clean[-(days + 1)]
        if base == 0:
            return None
        return round((last / base - 1.0) * 100.0, 2)

    out["ret_1m_pct"] = _ret(21)
    out["ret_3m_pct"] = _ret(63)
    out["ret_6m_pct"] = _ret(126)
    out["ret_12m_pct"] = _ret(252)

    high = max(clean[-252:]) if clean else None
    if high:
        out["pct_of_52wk_high"] = round((last / high) * 100.0, 2)

    out["rsi_14"] = compute_rsi_14(clean)

    def _ma(window: int) -> Optional[float]:
        if len(clean) < window:
            return None
        return sum(clean[-window:]) / float(window)

    ma50 = _ma(50)
    ma200 = _ma(200)
    if ma50:
        out["above_50dma_pct"] = round((last / ma50 - 1.0) * 100.0, 2)
    if ma200:
        out["above_200dma_pct"] = round((last / ma200 - 1.0) * 100.0, 2)
    return out


def fetch_price_series(tickers: Sequence[str]) -> Dict[str, List[float]]:
    """One batched yfinance download for a chunk → close-series per ticker."""
    return fetch_chunk_history(tickers)


def fetch_fundamentals_extended(ticker: str) -> Dict[str, Any]:
    """yfinance ``.info`` fundamentals snapshot (reused from the Actionable screener)."""
    return fetch_fundamentals(ticker)


def fetch_operating_metrics(ticker: str) -> Dict[str, Any]:
    """Backlog / RPO / bookings — unavailable in the MVP (Phase 3 hook)."""
    return {"available": False}


def fetch_evidence(ticker: str) -> Dict[str, Any]:
    """News / filing / transcript demand evidence — unavailable in the MVP (Phase 3 hook)."""
    return {"available": False, "demand_evidence": [], "positive_keywords": [], "negative_keywords": []}
