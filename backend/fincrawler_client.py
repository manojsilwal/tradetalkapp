"""
FinCrawler Client — async HTTP client for the deployed FinCrawler service.

FinCrawler is used in TradeTalk for:
  - Rich news articles (full text, not just titles)
  - SEC filings (10-K, 10-Q, 8-K text)
  - Any arbitrary URL scrape (hedge fund letters, earnings whisper, etc.)
  - Company-specific news (LLM-native text, no extra formatting step)

yfinance is still used for:
  - Live prices / % change
  - Structured fundamentals (P/E, market cap, vol)
  - Historical OHLCV
  - S&P 500 batch movers

Usage:
  from .fincrawler_client import fc
  text = await fc.scrape_text("https://www.wsj.com/markets/stocks/aapl")
  news = await fc.get_stock_news("NVDA")
  filing = await fc.get_sec_filing("MSFT", form="10-K")
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── In-process L1 cache (TTL = 24 h, mirrors FinCrawler's server-side cache) ─
_cache: Dict[str, tuple[float, Any, float]] = {}
_CACHE_TTL = 86_400  # 24 hours
_QUOTE_CACHE_TTL = 60  # spot quotes — short TTL for parity / live UI
_HTML_CACHE_TTL = 90  # slickcharts tables — short TTL, refreshed on page load


def _cache_set(key: str, val: Any, *, ttl: Optional[float] = None) -> None:
    _cache[key] = (time.time(), val, ttl if ttl is not None else _CACHE_TTL)


def _cache_get(key: str) -> Optional[Any]:
    if key in _cache:
        ts, val, ttl = _cache[key]
        if time.time() - ts < ttl:
            return val
        del _cache[key]
    return None


class FinCrawlerClient:
    """Async client for the FinCrawler API."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("FINCRAWLER_URL", "").rstrip("/")
        self.api_key = os.environ.get("FINCRAWLER_KEY", "")
        self.timeout = 20.0
        self._enabled: Optional[bool] = None
        self._sem: Optional[asyncio.Semaphore] = None

    def _concurrency_sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            try:
                n = max(1, min(int(os.environ.get("FINCRAWLER_MAX_CONCURRENCY", "6")), 20))
            except (TypeError, ValueError):
                n = 6
            self._sem = asyncio.Semaphore(n)
        return self._sem

    @property
    def enabled(self) -> bool:
        """True only when both env vars are configured."""
        if self._enabled is None:
            self._enabled = bool(self.base_url and self.api_key)
            if not self._enabled:
                logger.warning(
                    "[FinCrawler] FINCRAWLER_URL or FINCRAWLER_KEY not set — "
                    "FinCrawler tools will be skipped."
                )
        return self._enabled

    def _headers(self) -> Dict[str, str]:
        # FinCrawler `/v1/*` accepts Bearer or x-api-key; native `/scrape` uses X-Api-Key only.
        # Send both so either route works when API_KEY is configured.
        h: Dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TradeTalk/1.0",
        }
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    async def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        """Raw GET, returns parsed JSON. Raises on error."""
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url, headers=self._headers(), params=params or {})
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, body: Dict) -> Any:
        """Raw POST, returns parsed JSON. Raises on error."""
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, headers=self._headers(), json=body)
            r.raise_for_status()
            return r.json()

    # ── High-level helpers ─────────────────────────────────────────────────────

    async def scrape_text(self, url: str, use_cache: bool = True) -> str:
        """
        Scrape a URL and return clean text (LLM-ready).
        Cache is 24 h client-side + FinCrawler server-side.
        """
        if not self.enabled:
            return ""
        cache_key = f"scrape:{url}"
        if use_cache:
            cached = _cache_get(cache_key)
            if cached is not None:
                logger.debug("[FinCrawler] cache hit: %s", url)
                return cached

        try:
            # Firecrawl-compatible JSON body + Bearer auth (see FinCrawler `firecrawl_compat.py`).
            data = await self._post(
                "/v1/scrape",
                {"url": url, "formats": ["markdown"]},
            )
            text = ""
            if data.get("success") is False:
                logger.warning("[FinCrawler] scrape_text failed: %s", data.get("error"))
                return ""
            if data.get("success") and isinstance(data.get("data"), dict):
                inner = data["data"]
                text = inner.get("markdown") or inner.get("content") or ""
            else:
                text = data.get("text") or data.get("content") or ""
            if use_cache:
                _cache_set(cache_key, text)
            return text
        except Exception as e:
            logger.warning("[FinCrawler] scrape_text failed for %s: %s", url, e)
            return ""

    async def scrape_many(self, urls: List[str]) -> Dict[str, str]:
        """Parallel scrape of multiple URLs. Returns {url: text}."""
        if not self.enabled:
            return {}
        tasks = {url: self.scrape_text(url) for url in urls}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            url: (r if isinstance(r, str) else "")
            for url, r in zip(tasks.keys(), results)
        }

    async def get_stock_news(self, ticker: str, limit: int = 8) -> List[str]:
        """
        Return a list of LLM-ready article summaries for a given ticker.
        Uses FinCrawler's Yahoo Finance scrape (rendered page, not private API).
        Falls back to empty list if FinCrawler is unavailable.
        """
        if not self.enabled:
            return []
        ticker = ticker.upper().strip()
        cache_key = f"news:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            # FinCrawler: scrape Yahoo Finance news page for ticker
            yahoo_news_url = f"https://finance.yahoo.com/quote/{ticker}/news"
            text = await self.scrape_text(yahoo_news_url, use_cache=False)
            # Also try a direct news endpoint if FinCrawler exposes one
            try:
                data = await self._get("/news", params={"ticker": ticker, "limit": limit})
                articles = data.get("articles") or []
                summaries = [
                    f"{a.get('title','')}: {a.get('summary') or a.get('text','')[:200]}"
                    for a in articles[:limit]
                ]
            except Exception:
                # fallback: parse raw scraped text into lines
                summaries = [line.strip() for line in text.split("\n") if len(line.strip()) > 60][:limit]

            _cache_set(cache_key, summaries)
            return summaries
        except Exception as e:
            logger.warning("[FinCrawler] get_stock_news failed for %s: %s", ticker, e)
            return []

    async def get_stock_news_articles(self, ticker: str, limit: int = 12) -> List[Dict[str, str]]:
        """
        Structured news articles from GET /news when FinCrawler exposes it.
        Returns list of {title, summary, publisher, link, source}.
        """
        if not self.enabled:
            return []
        ticker = ticker.upper().strip()
        cache_key = f"news_articles:{ticker}:{limit}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        articles: List[Dict[str, str]] = []
        try:
            data = await self._get("/news", params={"ticker": ticker, "limit": limit})
            for item in (data.get("articles") or [])[:limit]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                summary = str(item.get("summary") or item.get("text") or "").strip()[:500]
                articles.append(
                    {
                        "title": title,
                        "summary": summary,
                        "publisher": str(item.get("publisher") or item.get("source") or "FinCrawler").strip(),
                        "link": str(item.get("link") or item.get("url") or "").strip(),
                        "source": "fincrawler",
                    }
                )
        except Exception as e:
            logger.debug("[FinCrawler] get_stock_news_articles /news failed for %s: %s", ticker, e)

        if not articles:
            summaries = await self.get_stock_news(ticker, limit=limit)
            for line in summaries:
                text = str(line or "").strip()
                if not text:
                    continue
                if ": " in text:
                    title, _, summary = text.partition(": ")
                else:
                    title, summary = text[:120], text
                articles.append(
                    {
                        "title": title.strip(),
                        "summary": summary.strip(),
                        "publisher": "FinCrawler",
                        "link": "",
                        "source": "fincrawler_scrape",
                    }
                )

        _cache_set(cache_key, articles)
        return articles

    async def get_sec_filing(
        self,
        ticker: str,
        form: str = "10-K",
        max_chars: int = 6000,
    ) -> str:
        """
        Fetch the most recent SEC filing text for a ticker.
        Returns LLM-ready extracted text (first max_chars characters of the filing).
        """
        if not self.enabled:
            return ""
        ticker = ticker.upper().strip()
        cache_key = f"sec:{ticker}:{form}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            data = await self._get("/sec", params={"ticker": ticker, "form": form})
            text = data.get("text") or data.get("content") or ""
            if not text:
                # Fallback: scrape EDGAR directly
                edgar_url = (
                    f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
                    f"&dateRange=custom&startdt=2024-01-01&forms={form}"
                )
                text = await self.scrape_text(edgar_url)
            result = text[:max_chars] if text else f"No {form} filing found for {ticker}."
            _cache_set(cache_key, result, ttl=30 * 86400)
            return result
        except Exception as e:
            logger.warning("[FinCrawler] get_sec_filing failed for %s %s: %s", ticker, form, e)
            return f"SEC filing unavailable for {ticker} ({form}): {e}"

    async def health_check(self) -> bool:
        """Return True if FinCrawler is reachable."""
        if not self.enabled:
            return False
        try:
            await self._get("/health")
            return True
        except Exception:
            return False

    def _quote_timeout_s(self) -> float:
        try:
            return max(2.0, min(float(os.environ.get("FINCRAWLER_QUOTE_TIMEOUT_S", "8")), 30.0))
        except (TypeError, ValueError):
            return 8.0

    def _parse_quote_payload(self, data: Any) -> Optional[float]:
        if not isinstance(data, dict):
            return None
        if not data.get("ok"):
            return None
        raw = data.get("price")
        if raw is None:
            return None
        try:
            p = float(raw)
            return p if p > 0 else None
        except (TypeError, ValueError):
            return None

    def get_quote_price_sync(self, ticker: str) -> Optional[float]:
        """
        Sync spot price via FinCrawler GET /quote (for quote_fallbacks / asyncio.to_thread).
        """
        if not self.enabled:
            return None
        ticker = ticker.upper().strip()
        if not ticker:
            return None
        cache_key = f"quote_sync:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        url = f"{self.base_url}/quote"
        timeout = self._quote_timeout_s()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(
                    url,
                    headers=self._headers(),
                    params={"ticker": ticker},
                )
                r.raise_for_status()
                price = self._parse_quote_payload(r.json())
        except Exception as e:
            logger.warning("[FinCrawler] get_quote_price_sync failed for %s: %s", ticker, e)
            return None

        if price is not None:
            _cache_set(cache_key, price, ttl=_QUOTE_CACHE_TTL)
        return price

    async def fetch_html(self, url: str, *, force_refresh: bool = False) -> str:
        """Fetch raw HTML via FinCrawler GET /fetch/html (Tier-1 compliant fetch)."""
        if not self.enabled:
            return ""
        target = (url or "").strip()
        if not target:
            return ""
        cache_key = f"html:{target}"
        if not force_refresh:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached
        try:
            data = await self._get(
                "/fetch/html",
                params={"url": target, "force_refresh": str(force_refresh).lower()},
            )
            html = ""
            if isinstance(data, dict) and data.get("ok"):
                html = str(data.get("html") or "")
        except Exception as e:
            logger.warning("[FinCrawler] fetch_html failed for %s: %s", target, e)
            return ""
        if html and not force_refresh:
            _cache_set(cache_key, html, ttl=_HTML_CACHE_TTL)
        return html

    def fetch_html_sync(self, url: str, *, force_refresh: bool = False) -> str:
        """Sync HTML fetch for daily-brief / slickcharts paths."""
        if not self.enabled:
            return ""
        target = (url or "").strip()
        if not target:
            return ""
        cache_key = f"html_sync:{target}"
        if not force_refresh:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached
        timeout = max(self._quote_timeout_s(), 15.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(
                    f"{self.base_url}/fetch/html",
                    headers=self._headers(),
                    params={"url": target, "force_refresh": str(force_refresh).lower()},
                )
                r.raise_for_status()
                data = r.json()
            html = str(data.get("html") or "") if isinstance(data, dict) and data.get("ok") else ""
        except Exception as e:
            logger.warning("[FinCrawler] fetch_html_sync failed for %s: %s", target, e)
            return ""
        if html and not force_refresh:
            _cache_set(cache_key, html, ttl=_HTML_CACHE_TTL)
        return html

    async def get_quote_price(self, ticker: str) -> Optional[float]:
        """
        Spot price via FinCrawler GET /quote (Yahoo quote page scrape on the crawler host).
        Returns None if disabled, unreachable, or parse fails.
        """
        if not self.enabled:
            return None
        ticker = ticker.upper().strip()
        if not ticker:
            return None
        cache_key = f"quote:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            data = await self._get("/quote", params={"ticker": ticker})
            price = self._parse_quote_payload(data)
        except Exception as e:
            logger.warning("[FinCrawler] get_quote_price failed for %s: %s", ticker, e)
            return None
        if price is not None:
            _cache_set(cache_key, price, ttl=_QUOTE_CACHE_TTL)
        return price

    def _parse_fundamentals_payload(self, data: Any, ticker: str) -> Dict[str, Any]:
        if not isinstance(data, dict) or not data.get("ok"):
            return {}
        raw = data.get("data")
        if not isinstance(raw, dict):
            raw = {}
        market_cap = raw.get("marketCap")
        pe = raw.get("trailingPE")
        change_pct = raw.get("regularMarketChangePercent")
        return {
            "ticker": ticker.upper(),
            "company_name": raw.get("shortName") or ticker.upper(),
            "market_cap": market_cap,
            "pe_ratio": pe,
            "forward_pe": pe,
            "regular_market_price": raw.get("regularMarketPrice"),
            "change_pct": change_pct,
            "volume": raw.get("regularMarketVolume"),
            "source": "fincrawler",
        }

    async def get_fundamentals(self, ticker: str, *, force_refresh: bool = False) -> Dict[str, Any]:
        """Structured fundamentals via FinCrawler GET /quote/smart."""
        if not self.enabled:
            return {}
        ticker = ticker.upper().strip()
        if not ticker:
            return {}
        cache_key = f"fundamentals:{ticker}"
        if not force_refresh:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached
        try:
            data = await self._get(
                "/quote/smart",
                params={"ticker": ticker, "force_refresh": str(force_refresh).lower()},
            )
            parsed = self._parse_fundamentals_payload(data, ticker)
            if parsed:
                _cache_set(cache_key, parsed, ttl=300.0)
            return parsed
        except Exception as e:
            logger.warning("[FinCrawler] get_fundamentals failed for %s: %s", ticker, e)
            return {}

    def get_fundamentals_sync(self, ticker: str) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        ticker = ticker.upper().strip()
        if not ticker:
            return {}
        cache_key = f"fundamentals_sync:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.base_url}/quote/smart"
        timeout = self._quote_timeout_s()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(
                    url,
                    headers=self._headers(),
                    params={"ticker": ticker},
                )
                r.raise_for_status()
                parsed = self._parse_fundamentals_payload(r.json(), ticker)
        except Exception as e:
            logger.warning("[FinCrawler] get_fundamentals_sync failed for %s: %s", ticker, e)
            return {}
        if parsed:
            _cache_set(cache_key, parsed, ttl=300.0)
        return parsed

    async def get_fundamentals_many(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch fundamentals with concurrency cap."""
        if not self.enabled or not tickers:
            return {}
        sem = self._concurrency_sem()
        out: Dict[str, Dict[str, Any]] = {}

        async def _one(sym: str) -> None:
            async with sem:
                row = await self.get_fundamentals(sym)
                if row:
                    out[sym.upper()] = row

        await asyncio.gather(*[_one(t) for t in tickers], return_exceptions=True)
        return out


# Module-level singleton — import and use everywhere
fc = FinCrawlerClient()
