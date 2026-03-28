"""
In-memory L1 snapshot for hot market fields (refreshed by APScheduler, not per chat message).

Message handlers read via get_snapshot() — O(1) dict access. refresh() refetches macro + key tickers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_data: Dict[str, Any] = {}
_updated_at: float = 0.0
_lock = asyncio.Lock()


def get_snapshot() -> Dict[str, Any]:
    """Return last refreshed market snapshot (may be empty before first refresh)."""
    return dict(_data) if _data else {}


def updated_at_epoch() -> float:
    return _updated_at


async def refresh() -> None:
    """Populate L1 from MacroHealthConnector + fast yfinance quotes."""
    global _data, _updated_at
    async with _lock:
        try:
            from .connectors.macro import MacroHealthConnector

            macro = await MacroHealthConnector().fetch_data()
            ind = dict((macro or {}).get("indicators") or {})

            def _quotes():
                import yfinance as yf

                out: Dict[str, float] = {}
                # Macro ETFs + Magnificent 7 + Sector ETFs (10 SPDR sectors)
                for sym in (
                    "SPY", "QQQ", "IWM", "GLD", "UUP",
                    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BTC-USD",
                    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
                ):
                    try:
                        p = yf.Ticker(sym).fast_info.get("lastPrice") or yf.Ticker(sym).info.get(
                            "regularMarketPrice"
                        )
                        if p:
                            out[sym] = float(p)
                    except Exception:
                        continue
                return out

            quotes = await asyncio.to_thread(_quotes)
            # Split quotes into logical groups for easier consumption
            sector_syms = {"XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE"}
            sector_quotes = {k: v for k, v in quotes.items() if k in sector_syms}
            equity_quotes = {k: v for k, v in quotes.items() if k not in sector_syms}
            _data = {
                "macro_indicators": ind,
                "quotes": equity_quotes,
                "sector_etfs": sector_quotes,
                "vix_level": ind.get("vix_level"),
                "credit_stress_index": ind.get("credit_stress_index"),
            }
            _updated_at = time.time()
            logger.info("[MarketL1] refreshed quotes=%d equity, %d sector ETFs", len(equity_quotes), len(sector_quotes))
        except Exception as e:
            logger.warning("[MarketL1] refresh failed: %s", e)
