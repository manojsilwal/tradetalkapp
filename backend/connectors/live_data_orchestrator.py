"""
Parallel live-data orchestrator — division of labor between yfinance and FinCrawler.

yfinance: prices / % change / volume (batch fast_info)
FinCrawler: fundamentals (/quote/smart), news (/news), SEC (/sec) — concurrently
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .yfinance_capability import record_failure, record_success, should_attempt

logger = logging.getLogger(__name__)

WantCategory = str  # price | fundamentals | news | sec


def _deadline_s() -> float:
    try:
        return max(0.5, float(os.environ.get("LIVE_DATA_ORCHESTRATOR_DEADLINE_S", "3.0")))
    except (TypeError, ValueError):
        return 3.0


def _news_symbol_limit() -> int:
    try:
        return max(1, min(int(os.environ.get("LIVE_DATA_NEWS_SYMBOL_LIMIT", "10")), 25))
    except (TypeError, ValueError):
        return 10


@dataclass
class LiveDataBundle:
    quotes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fundamentals: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    news: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    sec: Dict[str, Any] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)
    partial: bool = False
    elapsed_s: float = 0.0

    def to_meta(self) -> Dict[str, Any]:
        return {
            "live_data_sources": dict(self.sources),
            "live_data_partial": self.partial,
            "live_data_elapsed_s": round(self.elapsed_s, 3),
        }


async def _fetch_yfinance_prices(
    symbols: Sequence[str],
    *,
    force: bool,
) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    if not symbols or not should_attempt("price"):
        return {}, False

    def _run() -> Dict[str, Dict[str, Any]]:
        from backend.market_intel import fetch_realtime_quotes

        return fetch_realtime_quotes(list(symbols), force=force)

    try:
        quotes = await asyncio.to_thread(_run)
        if quotes:
            record_success("price")
            return quotes, False
        record_failure("price")
        return {}, True
    except Exception as exc:
        logger.warning("[LiveDataOrchestrator] yfinance price batch failed: %s", exc)
        record_failure("price")
        return {}, True


async def _fetch_fincrawler_prices(
    symbols: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """Spot prices via FinCrawler when yfinance price breaker is open or batch failed."""
    from backend.fincrawler_client import fc

    if not fc.enabled:
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    async def _one(sym: str) -> None:
        price = await fc.get_quote_price(sym)
        if price is not None and price > 0:
            out[sym.upper()] = {"price": price, "pct": None, "previous_close": None}

    await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=True)
    return out


async def _fetch_fundamentals_many(symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    from backend.fincrawler_client import fc

    if not fc.enabled or not symbols:
        return {}
    return await fc.get_fundamentals_many(list(symbols))


async def _fetch_news_many(symbols: Sequence[str], *, limit: int) -> Dict[str, List[Dict[str, Any]]]:
    from backend.fincrawler_client import fc

    if not fc.enabled or not symbols:
        return {}

    capped = list(symbols)[:_news_symbol_limit()]
    out: Dict[str, List[Dict[str, Any]]] = {}

    async def _one(sym: str) -> None:
        articles = await fc.get_stock_news_articles(sym, limit=limit)
        if articles:
            out[sym.upper()] = articles

    await asyncio.gather(*[_one(s) for s in capped], return_exceptions=True)
    return out


async def _fetch_sec(focus_ticker: Optional[str]) -> Dict[str, Any]:
    from backend.fincrawler_client import fc

    sym = (focus_ticker or "").upper().strip()
    if not sym or not fc.enabled:
        return {}
    text = await fc.get_sec_filing(sym, form="10-K", max_chars=4000)
    if not text or text.startswith("SEC filing unavailable"):
        return {}
    return {"ticker": sym, "form": "10-K", "excerpt": text[:500], "text": text}


async def fetch_live_bundle(
    symbols: Sequence[str],
    *,
    want: Sequence[WantCategory] = ("price", "fundamentals", "news", "sec"),
    focus_ticker: Optional[str] = None,
    deadline_s: Optional[float] = None,
    force: bool = False,
    news_per_symbol: int = 3,
) -> LiveDataBundle:
    """
    Fetch complementary live data in parallel.

    Returns a bundle with quotes (yfinance or FinCrawler fallback), fundamentals,
    news, and optional SEC excerpt for focus_ticker.
    """
    t0 = time.perf_counter()
    deadline = deadline_s if deadline_s is not None else _deadline_s()
    want_set = {w.strip().lower() for w in want}
    syms = sorted({(s or "").upper().strip() for s in symbols if (s or "").strip()})
    bundle = LiveDataBundle()
    partial = False

    async def _price_task() -> Tuple[Dict[str, Dict[str, Any]], str]:
        quotes, failed = await _fetch_yfinance_prices(syms, force=force)
        if quotes:
            return quotes, "yfinance"
        if not should_attempt("price") or failed:
            fc_quotes = await _fetch_fincrawler_prices(syms)
            if fc_quotes:
                return fc_quotes, "fincrawler"
        return {}, "none"

    tasks: Dict[str, asyncio.Task] = {}
    if "price" in want_set and syms:
        tasks["price"] = asyncio.create_task(_price_task())
    if "fundamentals" in want_set and syms:
        tasks["fundamentals"] = asyncio.create_task(_fetch_fundamentals_many(syms))
    if "news" in want_set and syms:
        tasks["news"] = asyncio.create_task(_fetch_news_many(syms, limit=news_per_symbol))
    if "sec" in want_set and focus_ticker:
        tasks["sec"] = asyncio.create_task(_fetch_sec(focus_ticker))

    async def _run_all() -> bool:
        nonlocal partial
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning("[LiveDataOrchestrator] task %s failed: %s", key, result)
                partial = True
                continue
            if key == "price":
                quotes, source = result
                bundle.quotes = quotes or {}
                if bundle.quotes:
                    bundle.sources["price"] = source
            elif key == "fundamentals":
                bundle.fundamentals = result or {}
                if bundle.fundamentals:
                    bundle.sources["fundamentals"] = "fincrawler"
            elif key == "news":
                bundle.news = result or {}
                if bundle.news:
                    bundle.sources["news"] = "fincrawler"
            elif key == "sec":
                bundle.sec = result or {}
                if bundle.sec:
                    bundle.sources["sec"] = "fincrawler"

    try:
        await asyncio.wait_for(_run_all(), timeout=deadline)
    except asyncio.TimeoutError:
        partial = True
        for t in tasks.values():
            if not t.done():
                t.cancel()
        logger.warning(
            "[LiveDataOrchestrator] deadline %.1fs exceeded for %d symbols",
            deadline,
            len(syms),
        )

    bundle.partial = partial
    bundle.elapsed_s = time.perf_counter() - t0
    return bundle


def fetch_live_bundle_sync(
    symbols: Sequence[str],
    **kwargs: Any,
) -> LiveDataBundle:
    """Sync wrapper for brief builders (no running event loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fetch_live_bundle(symbols, **kwargs))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(fetch_live_bundle(symbols, **kwargs))).result()


def apply_quotes_to_row(row: Dict[str, Any], quotes: Dict[str, Dict[str, Any]]) -> bool:
    sym = (row.get("symbol") or "").upper()
    q = quotes.get(sym)
    if not q:
        return False
    if q.get("price") is not None:
        row["close"] = q["price"]
    if q.get("pct") is not None:
        row["daily_return_pct"] = q["pct"]
    if q.get("previous_close") is not None:
        row["_rt_previous_close"] = q["previous_close"]
    return True


def apply_bundle_enrichment(row: Dict[str, Any], bundle: LiveDataBundle) -> None:
    """Attach FinCrawler fundamentals and news headlines to a mover/holding row."""
    sym = (row.get("symbol") or "").upper()
    fund = bundle.fundamentals.get(sym)
    if fund:
        for key in ("market_cap", "pe_ratio", "forward_pe", "company_name", "industry", "sector"):
            if fund.get(key) is not None:
                row[key] = fund[key]
        row["enrichment_source"] = fund.get("source") or "fincrawler"

    articles = bundle.news.get(sym) or []
    if articles:
        first = articles[0]
        headline = str(first.get("title") or "").strip()
        if headline and not row.get("primary_cause_headline"):
            row["primary_cause_headline"] = headline
            row["catalyst_status"] = "symbol_specific"
            row["primary_cause_category"] = "news"
        row["fincrawler_news"] = articles[:3]


def merge_bundle_meta(payload: Dict[str, Any], bundle: LiveDataBundle) -> None:
    payload.update(bundle.to_meta())
    if bundle.sec:
        payload["fincrawler_sec_excerpt"] = bundle.sec
