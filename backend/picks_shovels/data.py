"""
Data layer for the Picks & Shovels Momentum Finder.

Reuses the Actionable screener's rate-limit-safe primitives:
  - ``actionable_companies.fetch_chunk_history``  → one batched yfinance download/chunk
  - ``actionable_companies.fetch_fundamentals``   → yfinance ``.info`` snapshot (1h cache)
  - ``actionable_companies.compute_rsi_14``       → RSI helper

Price-momentum and fundamentals come from the Actionable screener. Phase 3 adds
real operating metrics (yfinance quarterly revenue acceleration) and demand
evidence (news + optional SEC filings via ``evidence.py``). All Phase-3 fetchers
degrade to an explicit ``{"available": False}`` on any failure so the scorer keeps
those components neutral instead of fabricating values (Plan §18).
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence

from ..actionable_companies import (
    compute_rsi_14,
    fetch_chunk_history,
    fetch_fundamentals,
)

logger = logging.getLogger(__name__)


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


def _operating_metrics_enabled() -> bool:
    return os.environ.get("PICKS_SHOVELS_OPERATING_METRICS", "1").strip() != "0"


def fetch_operating_metrics(ticker: str) -> Dict[str, Any]:
    """
    Operating momentum from yfinance quarterly revenue (Plan §7.4).

    Computes sequential (QoQ) revenue growth and its acceleration. Backlog / RPO
    are not standardized in XBRL and stay ``None`` (the scorer blend renormalizes
    over present components — never fabricated). ``{"available": False}`` on failure.
    """
    if not _operating_metrics_enabled():
        return {"available": False}
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        df = None
        for attr in ("quarterly_income_stmt", "quarterly_financials"):
            try:
                candidate = getattr(t, attr)
            except Exception:
                candidate = None
            if candidate is not None and getattr(candidate, "empty", True) is False:
                df = candidate
                break
        if df is None:
            return {"available": False}

        revenue = None
        for label in ("Total Revenue", "TotalRevenue", "Total revenue", "Revenue"):
            if label in df.index:
                revenue = df.loc[label]
                break
        if revenue is None:
            return {"available": False}

        series = revenue.dropna().sort_index()  # ascending: oldest -> newest
        q = [float(x) for x in series.tolist() if x is not None and not math.isnan(float(x))]
        if len(q) < 2 or q[-2] == 0:
            return {"available": False}

        latest, prev = q[-1], q[-2]
        qoq = (latest / prev - 1.0) * 100.0
        out: Dict[str, Any] = {"available": True, "qoq_revenue_growth_pct": round(qoq, 2)}
        if len(q) >= 3 and q[-3]:
            prev_qoq = (prev / q[-3] - 1.0) * 100.0
            out["qoq_revenue_accel_pct"] = round(qoq - prev_qoq, 2)
        return out
    except Exception as e:
        logger.debug("[PicksShovels] operating metrics failed for %s: %s", ticker, e)
        return {"available": False}


def fetch_evidence(ticker: str, company_name: str = "") -> Dict[str, Any]:
    """News + optional SEC-filing demand evidence (Phase 3). Resilient to failure."""
    try:
        from . import evidence as ps_evidence

        return ps_evidence.fetch_demand_evidence(ticker, company_name)
    except Exception as e:
        logger.debug("[PicksShovels] evidence failed for %s: %s", ticker, e)
        return {"available": False, "demand_evidence": []}
