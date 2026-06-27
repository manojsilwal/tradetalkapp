"""
Data layer for the Narrative Rotation Radar (Plan NR-2).

Reuses the Picks & Shovels / Actionable rate-limit-safe primitives so the radar
adds no new external dependencies in the MVP:
  - ``picks_shovels.data.fetch_price_series``        batched yfinance closes/chunk
  - ``picks_shovels.data.fetch_fundamentals_extended`` yfinance ``.info`` (market cap)
  - ``picks_shovels.data.momentum_from_closes``      per-member momentum features

SPY is fetched the same way and used as the relative-strength benchmark.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence

from ..picks_shovels import data as ps_data

logger = logging.getLogger(__name__)

BENCHMARK = "SPY"


def fetch_closes(tickers: Sequence[str]) -> Dict[str, List[float]]:
    """Batched close-series fetch for a chunk of tickers (reused, rate-limit-safe)."""
    return ps_data.fetch_price_series(list(tickers))


def fetch_benchmark_closes() -> List[float]:
    series = ps_data.fetch_price_series([BENCHMARK])
    return series.get(BENCHMARK, [])


def fetch_market_cap(ticker: str) -> Dict[str, Any]:
    """yfinance fundamentals snapshot (we only need market_cap/sector for weighting)."""
    return ps_data.fetch_fundamentals_extended(ticker)


def momentum_from_closes(closes: Sequence[float]) -> Dict[str, Any]:
    return ps_data.momentum_from_closes(closes)


def build_member_row(ticker: str, closes: List[float], fundamentals: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the per-member input shape consumed by ``features.build_theme_features``."""
    return {
        "ticker": ticker.upper(),
        "closes": closes,
        "momentum": momentum_from_closes(closes),
        "fundamentals": fundamentals or {},
    }
