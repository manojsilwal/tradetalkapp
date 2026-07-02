"""Request-scoped market data bundle for cross-surface parity.

Build once per user-facing flow (decision terminal, analyze, debate) so debate,
valuation, and spot share the same inputs within a request.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .connectors.spot import SpotQuote, clear_spot_cache, resolve_spot
from .valuation_inputs import fetch_yfinance_valuation_snapshot

logger = logging.getLogger(__name__)

STALE_ANCHOR_MOVE_PCT = 0.15


@dataclass
class FundamentalsBundle:
    """Merged debate + valuation fundamentals for one ticker."""

    ticker: str
    debate_data: Dict[str, Any]
    valuation_ext: Dict[str, Any]
    merged: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.merged = build_fundamentals_merged(self.debate_data, self.valuation_ext)


def build_fundamentals_merged(
    debate_data: Dict[str, Any],
    valuation_ext: Dict[str, Any],
) -> Dict[str, Any]:
    """Single dict: valuation DCF inputs + debate display/momentum fields."""
    out: Dict[str, Any] = dict(valuation_ext or {})
    for key, val in (debate_data or {}).items():
        if val is not None:
            out[key] = val
    return out


@dataclass
class MarketContext:
    ticker: str
    spot: Optional[SpotQuote]
    debate_data: Dict[str, Any]
    valuation_ext: Dict[str, Any]
    fundamentals: FundamentalsBundle
    as_of_utc: str


def apply_spot_to_debate(
    debate_data: Dict[str, Any],
    spot: Optional[SpotQuote],
) -> Dict[str, Any]:
    """Align debate bundle price fields with the canonical spot resolver."""
    if not spot or not spot.price:
        return debate_data
    out = dict(debate_data)
    out["current_price"] = round(float(spot.price), 2)
    out["spot_price_source"] = spot.source
    out["market_data_degraded"] = bool(spot.degraded)
    if spot.momentum_anchor_usd is not None:
        out["momentum_anchor_price"] = round(float(spot.momentum_anchor_usd), 2)
    return out


def _fetch_spot_sync(
    ticker: str,
    momentum_anchor: Optional[float] = None,
) -> Optional[SpotQuote]:
    return resolve_spot(ticker, momentum_anchor_usd=momentum_anchor)


async def fetch_market_context(
    ticker: str,
    *,
    tool_registry: Any = None,
    force: bool = False,
) -> MarketContext:
    """Fetch debate, valuation snapshot, and spot once for a ticker."""
    from .connectors.debate_data import clear_debate_data_cache, fetch_debate_data

    sym = ticker.upper().strip()
    if force:
        clear_spot_cache(sym)
        clear_debate_data_cache(sym)

    async def _debate() -> Dict[str, Any]:
        if tool_registry is not None:
            return await tool_registry.invoke(
                "fetch_debate_data", {"ticker": sym}, timeout_s=90.0
            )
        return await fetch_debate_data(sym)

    debate_data, valuation_ext = await asyncio.gather(
        _debate(),
        asyncio.to_thread(fetch_yfinance_valuation_snapshot, sym),
    )
    anchor = None
    if isinstance(debate_data, dict):
        raw_anchor = debate_data.get("momentum_anchor_price")
        if raw_anchor is not None:
            try:
                anchor = float(raw_anchor)
            except (TypeError, ValueError):
                anchor = None

    spot = await asyncio.to_thread(_fetch_spot_sync, sym, anchor)
    debate_data = apply_spot_to_debate(debate_data, spot)
    fundamentals = FundamentalsBundle(
        ticker=sym,
        debate_data=debate_data,
        valuation_ext=valuation_ext,
    )
    return MarketContext(
        ticker=sym,
        spot=spot,
        debate_data=debate_data,
        valuation_ext=valuation_ext,
        fundamentals=fundamentals,
        as_of_utc=datetime.now(timezone.utc).isoformat(),
    )
