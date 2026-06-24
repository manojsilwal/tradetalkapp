"""
Data Trust Layer — canonical provenance-stamped spot/quote fetch.

One function so that *every* spot price carries its source + freshness. Wraps
the multi-provider fallback chain (Yahoo chart → Stooq → FinCrawler → yfinance)
and stamps a :class:`backend.schemas.DataFreshness` envelope onto the result.

``resolve_spot`` adds a module-level TTL cache so parallel dashboard endpoints
share the same resolved price within one analyze burst.

New code that needs a spot price should prefer this over calling the provider
chain directly; existing call sites can migrate incrementally.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from ..schemas import DataFreshness

# Providers we treat as a genuine live read (not a degraded fallback).
from ..freshness import LIVE_SPOT_PROVIDERS, spot_provider_degraded

_LIVE_PROVIDERS = LIVE_SPOT_PROVIDERS

SPOT_CACHE_TTL_S = float(os.environ.get("SPOT_CACHE_TTL_S", "60"))
_ACTIVITY_MAX = int(os.environ.get("SPOT_ACTIVITY_LOG_SIZE", "200"))

_spot_cache: Dict[str, Tuple["SpotQuote", float]] = {}

# Ring buffer of recent fetch events — consumed by Pipeline Ops page.
# Each entry: {ticker, price, source, degraded, cache_hit, ts_utc}
_spot_activity: list = []


def record_spot_fetch(ticker: str, price: Optional[float], source: str,
                      degraded: bool, cache_hit: bool) -> None:
    """Append a fetch event to the in-memory activity ring buffer."""
    global _spot_activity
    _spot_activity.append({
        "ticker": ticker,
        "price": price,
        "source": source,
        "degraded": degraded,
        "cache_hit": cache_hit,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    })
    if len(_spot_activity) > _ACTIVITY_MAX:
        _spot_activity = _spot_activity[-_ACTIVITY_MAX:]


def get_spot_activity(limit: int = 50) -> list:
    """Return the most recent spot fetch events, newest first."""
    return list(reversed(_spot_activity[-limit:]))


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def _spot_resolver_enabled() -> bool:
    return _env_flag("SPOT_RESOLVER_ENABLE", "1")


@dataclass(frozen=True)
class SpotQuote:
    price: float
    source: str
    captured_at_utc: str
    degraded: bool
    momentum_anchor_usd: Optional[float] = None


def clear_spot_cache(ticker: Optional[str] = None) -> None:
    """Invalidate spot cache (e.g. on force-refresh analyze)."""
    if ticker:
        _spot_cache.pop(ticker.upper().strip(), None)
    else:
        _spot_cache.clear()


def _yfinance_spot_fallback(sym: str) -> Optional[Tuple[float, str]]:
    """History last close, then .info price fields."""
    try:
        import yfinance as yf

        t = yf.Ticker(sym)
        hist = t.history(period="5d")
        if hist is not None and not hist.empty:
            close = float(hist["Close"].iloc[-1])
            if close > 0:
                return close, "yfinance_history"
        info = t.info or {}
        for key in ("regularMarketPrice", "currentPrice", "previousClose"):
            val = info.get(key)
            if val is not None:
                price = float(val)
                if price > 0:
                    return price, "yfinance_info"
    except Exception:
        pass
    return None


def _fetch_spot_chain(sym: str) -> Optional[Tuple[float, str]]:
    from .quote_fallbacks import fetch_us_equity_spot

    res = fetch_us_equity_spot(sym)
    if res is not None:
        return res
    return _yfinance_spot_fallback(sym)


def get_spot_with_freshness(
    ticker: str,
    *,
    strict_when_open: bool = False,
) -> Tuple[Optional[float], DataFreshness]:
    """Return ``(price, DataFreshness)`` for a US equity spot price."""
    from ..freshness import assess, assess_spot

    sym = (ticker or "").upper().strip()
    res = _fetch_spot_chain(sym) if sym else None

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
        fresh = assess(data_class="live_quote", source="none")
        return None, fresh

    price, provider = res
    degraded = spot_provider_degraded(provider)
    fresh = assess_spot(source=provider, degraded=degraded)
    return float(price), fresh


def resolve_spot(
    ticker: str,
    *,
    strict_when_open: bool = False,
    momentum_anchor_usd: Optional[float] = None,
    force_refresh: bool = False,
) -> Optional[SpotQuote]:
    """
    Canonical sync spot accessor with TTL cache.

    Returns ``None`` when no price can be resolved (unless strict_when_open raises).
    """
    if not _spot_resolver_enabled():
        price, fresh = get_spot_with_freshness(ticker, strict_when_open=strict_when_open)
        if price is None:
            return None
        return SpotQuote(
            price=float(price),
            source=str(fresh.source or "unknown"),
            captured_at_utc=datetime.now(timezone.utc).isoformat(),
            degraded=bool(fresh.degraded),
            momentum_anchor_usd=momentum_anchor_usd,
        )

    sym = (ticker or "").upper().strip()
    if not sym:
        return None

    if force_refresh:
        _spot_cache.pop(sym, None)

    now = time.monotonic()
    cached = _spot_cache.get(sym)
    if cached is not None:
        quote, expires = cached
        if now < expires:
            record_spot_fetch(sym, quote.price, quote.source, quote.degraded, cache_hit=True)
            if momentum_anchor_usd is not None and quote.momentum_anchor_usd != momentum_anchor_usd:
                return SpotQuote(
                    price=quote.price,
                    source=quote.source,
                    captured_at_utc=quote.captured_at_utc,
                    degraded=quote.degraded,
                    momentum_anchor_usd=momentum_anchor_usd,
                )
            return quote

    price, fresh = get_spot_with_freshness(sym, strict_when_open=strict_when_open)
    if price is None:
        record_spot_fetch(sym, None, "unavailable", True, cache_hit=False)
        return None

    captured = fresh.captured_at or datetime.now(timezone.utc).isoformat()
    quote = SpotQuote(
        price=float(price),
        source=str(fresh.source or "unknown"),
        captured_at_utc=captured,
        degraded=bool(fresh.degraded),
        momentum_anchor_usd=momentum_anchor_usd,
    )
    _spot_cache[sym] = (quote, now + SPOT_CACHE_TTL_S)
    record_spot_fetch(sym, quote.price, quote.source, quote.degraded, cache_hit=False)
    return quote
