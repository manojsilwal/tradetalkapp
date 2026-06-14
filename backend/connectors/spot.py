"""
Data Trust Layer — canonical provenance-stamped spot/quote fetch.

One function so that *every* spot price carries its source + freshness. Wraps
the existing multi-provider fallback chain (`fetch_us_equity_spot`: Stooq
-> Yahoo chart -> FinCrawler last) and stamps a :class:`backend.schemas.DataFreshness`
envelope onto the result.

Strict rule (Workstream C): if a regular session is open and no live quote can
be obtained, ``strict_when_open=True`` raises ``InsufficientDataError`` so the
caller surfaces a 503 instead of silently showing a missing/last-known number.

New code that needs a spot price should prefer this over calling the provider
chain directly; existing call sites can migrate incrementally.
"""
from __future__ import annotations

from typing import Optional, Tuple

from ..schemas import DataFreshness

# Providers we treat as a genuine live read (not a degraded fallback).
_LIVE_PROVIDERS = frozenset({"yahoo_chart", "yfinance", "yfinance_history", "yfinance_info"})


def get_spot_with_freshness(
    ticker: str,
    *,
    strict_when_open: bool = False,
) -> Tuple[Optional[float], DataFreshness]:
    """Return ``(price, DataFreshness)`` for a US equity spot price.

    ``price`` is ``None`` when no provider could supply a quote; in that case the
    returned envelope is marked stale (no ``captured_at``). Set
    ``strict_when_open=True`` to raise ``InsufficientDataError`` instead when the
    market is open.
    """
    from .quote_fallbacks import fetch_us_equity_spot
    from ..freshness import assess, assess_spot

    sym = (ticker or "").upper().strip()
    res = fetch_us_equity_spot(sym) if sym else None

    if res is None:
        if strict_when_open:
            from ..market_calendar import is_market_open

            if is_market_open():
                from ..data_errors import InsufficientDataError

                raise InsufficientDataError(
                    "quote",
                    f"No live quote available for {sym or ticker} during an open session.",
                    ticker=sym or ticker,
                    missing=["spot_price"],
                )
        # No quote, market closed (or non-strict): truthful stale/unverified envelope.
        fresh = assess(data_class="live_quote", source="none")
        return None, fresh

    price, provider = res
    degraded = provider not in _LIVE_PROVIDERS
    fresh = assess_spot(source=provider, degraded=degraded)
    return float(price), fresh
