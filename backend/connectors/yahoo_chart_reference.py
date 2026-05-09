"""
Yahoo Finance **chart** API (v8) — same reference surface as:

- ``tests/e2e/parity.spec.ts``
- ``e2e/helpers/yahooFinance.js``
- ``tests/e2e/llm-production-qa.spec.ts`` (``fetchYahooReference``)

Use this for parity checks so Python tests do not diverge from UI E2E (which use
``query1.finance.yahoo.com``, not only ``yfinance``'s internal scraping).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

CHART_URL_TEMPLATE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DEFAULT_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class YahooChartQuote:
    symbol: str
    regular_market_price: float
    chart_previous_close: Optional[float]
    change_pct: Optional[float]
    market_state: str
    regular_market_time: Optional[int]


def fetch_yahoo_chart_quote(
    symbol: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> YahooChartQuote:
    """Sync fetch; raises ``urllib.error.HTTPError`` on 4xx/5xx."""
    sym = (symbol or "").strip()
    if not sym:
        raise ValueError("Yahoo chart: empty symbol")

    path = urllib.parse.quote(sym, safe="")
    url = CHART_URL_TEMPLATE.format(symbol=path)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradeTalk/1.0 (yahoo chart parity reference)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError(f"Yahoo chart rate limited (429) for {sym}") from e
        raise

    result = (raw.get("chart") or {}).get("result")
    if not result:
        meta_err = (raw.get("chart") or {}).get("error")
        raise RuntimeError(f"Yahoo chart: no result for {sym}: {meta_err!r}")

    meta: Dict[str, Any] = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None or not isinstance(price, (int, float)):
        raise RuntimeError(f"Yahoo chart: missing regularMarketPrice for {sym}")
    price_f = float(price)
    if price_f <= 0:
        raise RuntimeError(f"Yahoo chart: invalid price for {sym}: {price_f}")

    prev_raw = meta.get("chartPreviousClose")
    if prev_raw is None:
        prev_raw = meta.get("previousClose")
    prev = float(prev_raw) if prev_raw is not None else None
    if prev is not None and prev <= 0:
        prev = None

    change_pct: Optional[float] = None
    if prev is not None:
        change_pct = ((price_f - prev) / prev) * 100.0

    rmt = meta.get("regularMarketTime")
    rmt_i = int(rmt) if rmt is not None else None

    sym_key = sym.upper()
    return YahooChartQuote(
        symbol=sym_key,
        regular_market_price=price_f,
        chart_previous_close=prev,
        change_pct=change_pct,
        market_state=str(meta.get("marketState") or "UNKNOWN"),
        regular_market_time=rmt_i,
    )


def fetch_yahoo_chart_quotes(
    symbols: list[str],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Dict[str, YahooChartQuote]:
    """Fetch many symbols; duplicates are de-duplicated."""
    out: Dict[str, YahooChartQuote] = {}
    seen: set[str] = set()
    for s in symbols:
        u = (s or "").strip().upper()
        if not u or u in seen:
            continue
        seen.add(u)
        q = fetch_yahoo_chart_quote(u, timeout_s=timeout_s)
        out[u] = q
    return out
