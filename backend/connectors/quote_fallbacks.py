"""
US equity spot fallbacks when yfinance history/quote is empty (e.g. cloud IP blocks).

Order: Stooq (no key) → FinCrawler GET /quote (optional; uses FINCRAWLER_URL + FINCRAWLER_KEY).
"""
from __future__ import annotations

import asyncio
import csv
import logging
import re
import urllib.error
import urllib.request
from io import StringIO
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_STOOQ_TIMEOUT_S = 8


def fetch_us_equity_spot(ticker: str) -> Optional[Tuple[float, str]]:
    """
    Sync entry point for use inside asyncio.to_thread(debate sync fetch).

    Returns (price, provider_label) or None.
    Only US-listed symbols supported for Stooq `.us` suffix per plan.
    """
    sym = (ticker or "").upper().strip()
    # US symbols only: AAPL, BRK.B → stooq `brk-b.us`
    if not sym or not re.match(r"^[A-Z]{1,6}(\.[A-Z])?$", sym):
        return None

    spot = _stooq_us_spot(sym)
    if spot is not None:
        logger.info("[QuoteFallbacks] spot from stooq ticker=%s price=%s", sym, spot)
        return (spot, "stooq")

    spot_fc = _fincrawler_quote_sync(sym)
    if spot_fc is not None:
        logger.info("[QuoteFallbacks] spot from fincrawler ticker=%s price=%s", sym, spot_fc)
        return (spot_fc, "fincrawler")

    return None


def _stooq_us_spot(symbol: str) -> Optional[float]:
    """Last close / quote from Stooq CSV for US suffix (.us)."""
    qsym = symbol.lower().replace(".", "-") + ".us"
    url = f"https://stooq.com/q/l/?s={qsym}&f=sd2t2ohlcv&h&e=csv"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TradeTalk/1.0 (quote fallback)"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_STOOQ_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug("[QuoteFallbacks] Stooq failed %s: %s", symbol, e)
        return None

    try:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None
        reader = csv.reader(StringIO(raw))
        rows = list(reader)
        if len(rows) < 2:
            return None
        header = [h.strip().lower() for h in rows[0]]
        last = rows[-1]
        if "close" in header:
            idx = header.index("close")
            return float(last[idx])
        # Fallback: last column often close
        return float(last[-1])
    except (ValueError, IndexError) as e:
        logger.debug("[QuoteFallbacks] Stooq parse failed %s: %s", symbol, e)
        return None


def _fincrawler_quote_sync(symbol: str) -> Optional[float]:
    from backend.fincrawler_client import fc

    if not fc.enabled:
        return None

    async def _run():
        return await fc.get_quote_price(symbol)

    try:
        return asyncio.run(_run())
    except RuntimeError:
        # Nested loop unlikely from to_thread worker; fallback
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
