"""
US equity spot fallbacks when yfinance history/quote is empty (e.g. cloud IP blocks).

Chain (``fetch_us_equity_spot``) — non-Yahoo by default:
  1. Stooq CSV — only when the response is real CSV (Stooq often serves a JS bot wall)
  2. Yahoo chart (query1) — only if ``QUOTE_FALLBACK_ALLOW_YAHOO_CHART=1``
  3. FinCrawler GET /quote when ``FINCRAWLER_URL`` + ``FINCRAWLER_KEY`` are set (last resort)

Configure ``backend/.env.local`` (see ``backend/.env.example``) with a reachable FinCrawler
URL so yfinance failures can still recover after keyless fallbacks fail.
"""
from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from io import StringIO
from typing import Optional, Tuple

import csv

logger = logging.getLogger(__name__)

_STOOQ_TIMEOUT_S = 8
_STOOQ_BROWSER_UA = (
    "Mozilla/5.0 (compatible; TradeTalk/1.0; +https://github.com/manojsilwal/tradetalkapp)"
)


def _is_html_bot_wall(raw: str) -> bool:
    """Stooq (and similar) return an HTML proof-of-work page instead of CSV."""
    if not raw:
        return True
    head = raw.lstrip()[:512].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return True
    if "__verify" in head or "requires javascript" in head:
        return True
    return False


def _yahoo_chart_meta(symbol: str) -> Optional[dict]:
    """Parse Yahoo chart meta for a symbol (price + session change %)."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    params = {"range": "1d", "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TradeTalk/1.0)"}
    try:
        req = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}",
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            if getattr(resp, "status", 200) == 429:
                return None
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug("[QuoteFallbacks] Yahoo chart failed %s: %s", sym, e)
        return None

    try:
        import json

        result = (json.loads(raw).get("chart") or {}).get("result") or []
        if not result:
            return None
        return result[0].get("meta") or {}
    except json.JSONDecodeError as e:
        logger.debug("[QuoteFallbacks] Yahoo chart parse failed %s: %s", sym, e)
        return None


def _yahoo_chart_spot(symbol: str) -> Optional[float]:
    """Last regularMarketPrice from Yahoo chart (indices like ^VIX supported)."""
    meta = _yahoo_chart_meta(symbol)
    if not meta:
        return None
    try:
        val = float(meta.get("regularMarketPrice"))
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def yahoo_chart_change_pct(symbol: str) -> Optional[float]:
    """Session % change from Yahoo chart meta (regularMarketPrice vs previous close)."""
    meta = _yahoo_chart_meta(symbol)
    if not meta:
        return None
    try:
        price = float(meta.get("regularMarketPrice"))
        prev_raw = meta.get("chartPreviousClose", meta.get("previousClose"))
        prev = float(prev_raw) if prev_raw is not None else None
        if price > 0 and prev and prev > 0:
            return ((price - prev) / prev) * 100.0
    except (TypeError, ValueError):
        return None
    return None


def _fincrawler_quote_sync(symbol: str) -> Optional[float]:
    from backend.fincrawler_client import fc

    if not fc.enabled:
        return None
    return fc.get_quote_price_sync(symbol)


def _stooq_us_spot(symbol: str) -> Optional[float]:
    """Last close from Stooq CSV for US suffix (.us). Returns None on bot wall or parse errors."""
    qsym = symbol.lower().replace(".", "-") + ".us"
    # stooq.com and stooq.pl share the same bot wall; try .com first for consistency.
    for host in ("stooq.com", "stooq.pl"):
        url = f"https://{host}/q/l/?s={qsym}&f=sd2t2ohlcv&h&e=csv"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": _STOOQ_BROWSER_UA, "Accept": "text/csv,*/*"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=_STOOQ_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug("[QuoteFallbacks] Stooq %s failed %s: %s", host, symbol, e)
            continue

        if _is_html_bot_wall(raw):
            logger.info(
                "[QuoteFallbacks] Stooq %s bot wall for %s — try Yahoo chart or FinCrawler last",
                host,
                symbol,
            )
            continue

        try:
            reader = csv.reader(StringIO(raw))
            rows = list(reader)
            if len(rows) < 2:
                continue
            header = [h.strip().lower() for h in rows[0]]
            last = rows[-1]
            if "close" in header:
                idx = header.index("close")
                price = float(last[idx])
            else:
                price = float(last[-1])
            if price > 0:
                return price
        except (ValueError, IndexError, csv.Error) as e:
            logger.debug("[QuoteFallbacks] Stooq parse failed %s@%s: %s", symbol, host, e)
    return None


def _allow_yahoo_chart_fallback() -> bool:
    return os.environ.get("QUOTE_FALLBACK_ALLOW_YAHOO_CHART", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def fetch_us_equity_spot(ticker: str) -> Optional[Tuple[float, str]]:
    """
    Sync entry point for use inside asyncio.to_thread(debate sync fetch).

    Returns (price, provider_label) or None.
    Precedence (locked for analysis parity): Yahoo chart → Stooq → FinCrawler.
    """
    sym = (ticker or "").upper().strip()
    if not sym:
        return None

    yahoo = _yahoo_chart_spot(sym)
    if yahoo is not None:
        logger.info("[QuoteFallbacks] spot from yahoo_chart ticker=%s price=%s", sym, yahoo)
        return (yahoo, "yahoo_chart")

    if re.match(r"^[A-Z]{1,6}(\.[A-Z])?$", sym):
        stooq = _stooq_us_spot(sym)
        if stooq is not None:
            logger.info("[QuoteFallbacks] spot from stooq ticker=%s price=%s", sym, stooq)
            return (stooq, "stooq")

    fc_spot = _fincrawler_quote_sync(sym)
    if fc_spot is not None:
        logger.info("[QuoteFallbacks] spot from fincrawler ticker=%s price=%s", sym, fc_spot)
        return (fc_spot, "fincrawler")

    return None


def quote_fallback_status() -> dict:
    """Lightweight diagnostics for logs / debug (no network I/O)."""
    from backend.fincrawler_client import fc

    chain = ["stooq"]
    if _allow_yahoo_chart_fallback():
        chain.append("yahoo_chart")
    chain.append("fincrawler")
    return {
        "fincrawler_configured": fc.enabled,
        "fincrawler_url": fc.base_url if fc.enabled else None,
        "allow_yahoo_chart": _allow_yahoo_chart_fallback(),
        "chain": chain,
        "stooq_note": "Stooq CSV often blocked by JS bot wall; FinCrawler is last resort",
    }
