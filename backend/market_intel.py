"""
Market Intelligence Layer (MIL) — preloaded on a schedule, served at 0ms.

All data is fetched in the background by APScheduler and stored in memory.
Chat agent tools, Decision Terminal, and Backtest read from this cache
with a single O(1) call — no per-request API calls for common queries.

**Live movers (chat):** `format_movers_reply_for_chat` uses a parallel Yahoo `fast_info` scan
(session % vs prior close, TTL-cached) over the curated universe, with fallback to the
scheduled 1d batch download. FinCrawler does not supply exchange rankings — it is for news/SEC/URLs.

Refresh cadence (configured in main.py via APScheduler):
  - Fast layer (quotes, movers, news, sector %):  every 5 minutes
  - Slow layer (FOMC, earnings, PCR):             every 30 minutes

Data available after first warm-up (~8s after startup):
  ├── top_losers / top_gainers — scheduled **1d batch** (yf.download) + optional **live** snapshot
  ├── headlines        — live Yahoo Finance RSS + yfinance news
  ├── sector_perf      — 10 SPDR sector ETFs with daily % change + name
  ├── fomc             — next FOMC meeting date + last rate decision
  ├── earnings         — upcoming earnings dates + last EPS surprise per ticker
  └── options_flow     — SPY put-call ratio (market fear gauge)
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────────────────
_data: Dict[str, Any] = {}
_updated_at: float = 0.0
_lock = asyncio.Lock()

# Live movers: parallel Yahoo fast_info (session % vs prior close), short TTL cache
_live_movers_lock = threading.Lock()
_live_movers_cache: Optional[Dict[str, Any]] = None
_live_movers_cache_ts: float = 0.0

# Full S&P 500 universe — use the comprehensive data_lake list for daily brief scanning
def _get_sp500_universe() -> List[str]:
    try:
        from .data_lake.config import SP500_TICKERS
        return list(SP500_TICKERS)
    except Exception:
        try:
            from .connectors.backtest_data import SP500_UNIVERSE
            return list(SP500_UNIVERSE)
        except Exception:
            # Fallback: broad large-cap list if imports fail
            return [
                "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
                "JPM", "JNJ", "UNH", "V", "PG", "HD", "MA", "XOM", "CVX",
                "ABBV", "MRK", "PFE", "WMT", "COST", "BAC", "LLY", "AVGO",
                "KO", "PEP", "TMO", "ORCL", "NFLX", "DIS", "ADBE", "CRM",
                "AMD", "INTC", "QCOM", "TXN", "MU", "SPY", "QQQ", "IWM",
            ]

_SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
}

_EARNINGS_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "JNJ", "V", "WMT", "HD", "BAC", "XOM",
]

_FOMC_DATES_FALLBACK = [
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


# ── Public API ────────────────────────────────────────────────────────────────

def get_intel() -> Dict[str, Any]:
    """Return last cached market intelligence (O(1) read, thread-safe)."""
    return dict(_data) if _data else {}


def is_stale(max_age_seconds: float = 600) -> bool:
    """Return True if the cache is older than max_age_seconds or empty."""
    return (time.time() - _updated_at) > max_age_seconds or not _data


def updated_at_epoch() -> float:
    return _updated_at


# ── Real-time quote overlay ───────────────────────────────────────────────────
_rt_quotes_lock = threading.Lock()
_rt_quotes_cache: Optional[Dict[str, Dict[str, Any]]] = None
_rt_quotes_cache_ts: float = 0.0
_RT_QUOTES_TTL = float(os.environ.get("RT_QUOTES_TTL_SEC", "60"))


def is_market_open() -> bool:
    """Return True if US equity market is currently in regular session (9:30–16:00 ET, weekdays)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def needs_realtime_overlay() -> bool:
    """
    Return True when real-time Yahoo quotes should overlay stale DB data.
    Covers the full trading day window: weekdays 4 AM – 11:59 PM ET.
    After market close, BigQuery EOD ingestion may lag by hours, so users
    still need fresh prices from Yahoo in the evening.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    # From 4 AM (pre-market) to midnight on weekdays
    return now_et.hour >= 4


def _fetch_single_rt_quote(sym: str) -> Optional[tuple]:
    """Fetch one real-time quote via yfinance fast_info. Returns (sym, {price, pct, prev_close})."""
    try:
        import yfinance as yf
        fi = yf.Ticker(sym).fast_info
        price = fi.get("lastPrice") or fi.get("regularMarketPrice")
        prev = fi.get("previousClose")
        if price is None or prev is None or float(prev) <= 0:
            return None
        price_f = round(float(price), 2)
        prev_f = round(float(prev), 2)
        pct = round((price_f - prev_f) / prev_f * 100.0, 2)
        return (sym, {"price": price_f, "pct": pct, "previous_close": prev_f})
    except Exception:
        return None


def fetch_realtime_quotes(symbols: List[str], *, force: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Parallel real-time quotes for a list of symbols via Yahoo fast_info.
    Returns {SYMBOL: {price, pct, previous_close}} with a short TTL cache.
    Only fetches on weekday trading windows unless force=True.
    """
    global _rt_quotes_cache, _rt_quotes_cache_ts

    if not force and not needs_realtime_overlay():
        return {}

    now = time.time()
    with _rt_quotes_lock:
        if (
            not force
            and _rt_quotes_cache is not None
            and (now - _rt_quotes_cache_ts) < _RT_QUOTES_TTL
        ):
            # Return subset of cached quotes for requested symbols
            return {s: _rt_quotes_cache[s] for s in symbols if s in _rt_quotes_cache}

    # Fetch in parallel
    t0 = time.time()
    results: Dict[str, Dict[str, Any]] = {}
    max_workers = min(30, len(symbols)) if symbols else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_single_rt_quote, sym): sym for sym in symbols}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    results[result[0]] = result[1]
            except Exception:
                pass

    elapsed = time.time() - t0
    logger.info("[MarketIntel] RT quotes: %d/%d symbols in %.2fs", len(results), len(symbols), elapsed)

    # Update cache with new results (merge with existing)
    with _rt_quotes_lock:
        if _rt_quotes_cache is None:
            _rt_quotes_cache = {}
        _rt_quotes_cache.update(results)
        _rt_quotes_cache_ts = time.time()

    return {s: results[s] for s in symbols if s in results}


def _fast_info_mover_row(sym: str) -> Optional[Dict[str, Any]]:
    """One symbol: last price vs previous close → session-style % (Yahoo may be delayed ~15m)."""
    try:
        import yfinance as yf

        fi = yf.Ticker(sym).fast_info
        price = fi.get("lastPrice") or fi.get("regularMarketPrice")
        prev = fi.get("previousClose")
        if price is None or prev is None or float(prev) <= 0:
            return None
        pct = (float(price) - float(prev)) / float(prev) * 100.0
        return {"sym": sym, "price": round(float(price), 2), "pct": round(pct, 2)}
    except Exception:
        return None


def _compute_live_movers_parallel() -> Dict[str, Any]:
    """
    Scan curated universe with a single batch download — much faster and doesn't exhaust connections.
    """
    import yfinance as yf
    syms = _get_sp500_universe()
    movers: List[Dict[str, Any]] = []
    t0 = time.time()
    try:
        raw = yf.download(
            tickers=syms,
            period="60d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        close = raw.get("Close")
        volume = raw.get("Volume")
        if close is not None and close.shape[0] >= 2:
            prev_close = close.iloc[-2]
            last_close = close.iloc[-1]
            pct_changes = ((last_close - prev_close) / prev_close * 100).dropna()
            
            # Compute daily returns for standard deviation and mean over the last 60 days
            daily_returns = close.pct_change() * 100
            mean_ret = daily_returns.mean()
            std_ret = daily_returns.std()
            
            # Compute average volume
            mean_vol = volume.mean() if volume is not None else None
            last_vol = volume.iloc[-1] if volume is not None else None
            
            for sym, pct in pct_changes.items():
                price = float(last_close.get(sym, 0))
                if price <= 0:
                    continue
                
                # Z-score of daily returns over 60 days
                z_val = 0.0
                sym_std = std_ret.get(sym) if std_ret is not None else None
                sym_mean = mean_ret.get(sym) if mean_ret is not None else None
                sym_daily_ret = daily_returns.iloc[-1].get(sym) if not daily_returns.empty else None
                if sym_std and sym_std > 0 and sym_mean is not None and sym_daily_ret is not None:
                    z_val = (sym_daily_ret - sym_mean) / sym_std
                
                # Relative volume (volume of last day / mean volume of last 60 days)
                rel_vol = 1.0
                sym_mean_vol = mean_vol.get(sym) if mean_vol is not None else None
                sym_last_vol = last_vol.get(sym) if last_vol is not None else None
                if sym_mean_vol and sym_mean_vol > 0 and sym_last_vol is not None:
                    rel_vol = sym_last_vol / sym_mean_vol
                
                vol_val = int(sym_last_vol) if sym_last_vol is not None else 0
                
                movers.append({
                    "sym": sym,
                    "price": round(price, 2),
                    "pct": round(float(pct), 2),
                    "volume": vol_val,
                    "relative_volume": round(float(rel_vol), 4),
                    "return_zscore_60d": round(float(z_val), 4),
                })
    except Exception as e:
        logger.warning("[MarketIntel] live movers batch download failed: %s", e)

    # Fallback to individual fast_info only for a very small set of priority tickers if batch fails
    if not movers:
        logger.info("[MarketIntel] falling back to priority tickers fast_info...")
        priority_syms = [
            "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
            "JPM", "JNJ", "UNH", "V", "PG", "HD", "MA", "XOM", "CVX",
            "ABBV", "MRK", "PFE", "WMT",
        ]
        for sym in priority_syms:
            row = _fast_info_mover_row(sym)
            if row:
                row.update({
                    "volume": 0,
                    "relative_volume": 1.0,
                    "return_zscore_60d": 0.0,
                })
                movers.append(row)

    movers.sort(key=lambda x: x["pct"])
    
    # Concurrently fetch news headlines for the top 5 losers and top 5 gainers
    extreme_movers = movers[:5] + list(reversed(movers))[:5]
    extreme_syms = list({m["sym"] for m in extreme_movers})
    
    news_map = {}
    if extreme_syms:
        def _get_ticker_news(sym: str) -> tuple[str, Dict[str, Any]]:
            try:
                import yfinance as yf
                ticker = yf.Ticker(sym)
                news = ticker.news or []
                if news:
                    first_article = news[0]
                    title = first_article.get("title", "")
                    title_lower = title.lower()
                    catalyst_keywords = (
                        "earnings", "dividend", "acquisition", "merger", "fda", "buyback",
                        "downgrade", "upgrade", "probe", "fraud", "miss", "beat", "ceo", "lawsuit", "guidance",
                        "regulatory", "investigation", "layoff", "restructur", "bankrupt"
                    )
                    has_cat = any(kw in title_lower for kw in catalyst_keywords)
                    category = "none"
                    if "earnings" in title_lower or "eps" in title_lower or "beat" in title_lower or "miss" in title_lower:
                        category = "earnings"
                    elif "dividend" in title_lower or "buyback" in title_lower:
                        category = "corporate_action"
                    elif has_cat:
                        category = "news"
                    
                    return sym, {
                        "catalyst_status": "symbol_specific" if has_cat else "no_catalyst",
                        "primary_cause_category": category,
                        "primary_cause_headline": title,
                        "primary_cause_weight": 0.8 if has_cat else 0.0
                    }
            except Exception:
                pass
            return sym, {
                "catalyst_status": "no_catalyst",
                "primary_cause_category": "none",
                "primary_cause_headline": "",
                "primary_cause_weight": 0.0
            }
            
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_sym = {executor.submit(_get_ticker_news, sym): sym for sym in extreme_syms}
            for future in as_completed(future_to_sym):
                try:
                    sym, cat_info = future.result()
                    news_map[sym] = cat_info
                except Exception:
                    pass

    # Update movers with catalyst info and default regime/other fields
    for m in movers:
        sym = m["sym"]
        cat_info = news_map.get(sym, {
            "catalyst_status": "no_catalyst",
            "primary_cause_category": "none",
            "primary_cause_headline": "",
            "primary_cause_weight": 0.0
        })
        m.update(cat_info)
        m.setdefault("market_regime", "Balanced")

    elapsed = time.time() - t0
    logger.info("[MarketIntel] live movers: %d/%d symbols in %.2fs", len(movers), len(syms), elapsed)
    return {
        "losers": movers[:25],
        "gainers": list(reversed(movers))[:25],
        "computed_at": time.time(),
        "n_scanned": len(syms),
        "n_ok": len(movers),
        "elapsed_s": round(elapsed, 2),
        "methodology": (
            "Source: Yahoo Finance batch download (last vs previous close). "
            "May be delayed ~15 minutes vs exchange; not identical to every public 'top losers' table."
        ),
    }



def get_live_movers_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Cached live movers snapshot (TTL default 120s). Thread-safe.
    Stale-while-revalidate: returns cached/fallback data immediately and
    revalidates in a background thread to prevent blocking web requests.
    """
    global _live_movers_cache, _live_movers_cache_ts
    ttl = float(os.environ.get("MIL_LIVE_MOVERS_TTL_SEC", "120"))
    now = time.time()
    
    # Check if cache is still fresh
    with _live_movers_lock:
        if (
            not force_refresh
            and _live_movers_cache is not None
            and (now - _live_movers_cache_ts) < ttl
        ):
            return _live_movers_cache

        # If cache is expired or missing, trigger revalidation in background thread
        # to avoid blocking the HTTP request thread.
        # We only start one background update at a time.
        is_updating = getattr(get_live_movers_snapshot, "_is_updating", False)
        
        if not is_updating:
            get_live_movers_snapshot._is_updating = True
            
            def _bg_update():
                try:
                    logger.info("[MarketIntel] revalidating live movers in background...")
                    snap = _compute_live_movers_parallel()
                    with _live_movers_lock:
                        global _live_movers_cache, _live_movers_cache_ts
                        _live_movers_cache = snap
                        _live_movers_cache_ts = time.time()
                except Exception as ex:
                    logger.warning("[MarketIntel] background live movers update failed: %s", ex)
                finally:
                    get_live_movers_snapshot._is_updating = False

            threading.Thread(target=_bg_update, daemon=True).start()

        # Return cached value if we have one (even if stale)
        if _live_movers_cache is not None:
            return _live_movers_cache

        # Otherwise, return fallback from get_intel() immediately
        intel = get_intel()
        fallback_snap = {
            "losers": intel.get("top_losers", []),
            "gainers": intel.get("top_gainers", []),
            "computed_at": now,
            "n_scanned": len(_get_sp500_universe()),
            "n_ok": len(intel.get("top_losers", [])) + len(intel.get("top_gainers", [])),
            "elapsed_s": 0.0,
            "methodology": "Source: Scheduled daily batch (yf.download 1d bars) cached in memory.",
        }
        return fallback_snap


def format_movers_reply_for_chat(direction: str) -> str:
    """
    Markdown block for chat + tools: prefer live parallel scan; fall back to scheduled 1d batch.
    direction: losers | gainers
    """
    direction = (direction or "losers").lower()
    if direction not in ("losers", "gainers"):
        direction = "losers"
    key = "top_losers" if direction == "losers" else "top_gainers"
    label = "TOP LOSERS" if direction == "losers" else "TOP GAINERS"

    live = get_live_movers_snapshot()
    rows = list(live["losers"] if direction == "losers" else live["gainers"])
    use_live = live.get("n_ok", 0) >= 5 and len(rows) >= 3

    if not use_live:
        intel = get_intel()
        rows = list(intel.get(key) or [])
        if not rows:
            return (
                "Mover data is still loading or unavailable. "
                "Do not invent tickers — ask the user to retry shortly."
            )
        batch_age = int(time.time() - _updated_at) if _updated_at else 0
        header = (
            f"[{label} — **scheduled daily batch** (yf.download 1d bars); "
            f"live fast_info scan returned too few symbols · batch age {batch_age}s]\n"
            "Other vendors may rank differently."
        )
    else:
        table_age = int(time.time() - float(live["computed_at"]))
        header = (
            f"[{label} — **session scan** (parallel Yahoo fast_info) · "
            f"{live['n_ok']}/{live['n_scanned']} names · table age {table_age}s · "
            f"compute {live['elapsed_s']}s]\n"
            f"{live['methodology']}"
        )

    lines = [header]
    for i, m in enumerate(rows[:15], 1):
        sign = "+" if m["pct"] >= 0 else ""
        lines.append(f"{i}. {m['sym']}: ${m['price']:.2f} ({sign}{m['pct']:.2f}%)")
    return "\n".join(lines)


def format_for_prompt() -> str:
    """
    Compact markdown block injected into the LLM system prompt on every chat session.
    Gives the agent ambient awareness of macro/market state without requiring a tool call.
    """
    d = get_intel()
    if not d:
        return ""

    lines: List[str] = [
        "## Live Market Intelligence (preloaded, ~5 min refresh)",
        "*(Top tickers below are a **short summary** only — for a full ranked list the assistant must use the **get_top_movers** tool or AUTHORITATIVE MOVER DATA.)*",
    ]

    # Sector performance
    sector = d.get("sector_perf") or {}
    if sector:
        leaders = sorted(sector.values(), key=lambda x: x.get("pct", 0), reverse=True)
        laggards = sorted(sector.values(), key=lambda x: x.get("pct", 0))
        top = leaders[0] if leaders else None
        bot = laggards[0] if laggards else None
        if top and bot:
            lines.append(
                f"- **Sectors**: Leading={top['name']} {top['pct']:+.1f}%, "
                f"Lagging={bot['name']} {bot['pct']:+.1f}%"
            )

    # Top movers summary (daily % vs prior close — same universe as chat get_top_movers)
    losers = d.get("top_losers") or []
    gainers = d.get("top_gainers") or []
    if losers:
        top3 = ", ".join(f"{m['sym']} {m['pct']:+.1f}%" for m in losers[:3])
        lines.append(f"- **Top losers (daily %, summary)**: {top3}")
    if gainers:
        top3 = ", ".join(f"{m['sym']} {m['pct']:+.1f}%" for m in gainers[:3])
        lines.append(f"- **Top gainers (daily %, summary)**: {top3}")

    # FOMC
    fomc = d.get("fomc") or {}
    if fomc and fomc.get("next_meeting"):
        lines.append(
            f"- **FOMC**: Next meeting {fomc['next_meeting']} "
            f"({fomc.get('days_until', '?')} days). "
            f"{fomc.get('last_decision', '')}"
        )

    # Options flow
    opts = d.get("options_flow") or {}
    if opts and not opts.get("error"):
        pcr = opts.get("spy_put_call_ratio")
        if pcr:
            lines.append(f"- **SPY Options PCR**: {pcr:.2f} — {opts.get('signal', '')}")

    # Upcoming earnings
    earnings = d.get("earnings") or {}
    near = [
        f"{t} ({v['days_until']}d, last {v['last_surprise_pct']})"
        for t, v in earnings.items()
        if v.get("days_until") is not None and 0 <= v["days_until"] <= 30
        and v.get("last_surprise_pct")
    ]
    if near:
        lines.append(f"- **Earnings soon**: {'; '.join(near[:4])}")

    # Recent headlines (top 5 only in prompt — full list available via tool)
    headlines = d.get("headlines") or []
    if headlines:
        lines.append("- **Recent headlines**: " + " | ".join(h[:80] for h in headlines[:5]))

    age = int(time.time() - _updated_at) if _updated_at else 0
    lines.append(f"*(data age: {age}s)*")

    return "\n".join(lines)


# ── Refresh orchestrators ─────────────────────────────────────────────────────

async def refresh_fast() -> None:
    """
    Fast refresh (every 5 min): movers, headlines, sector performance.
    These change frequently so we keep them fresh.
    """
    global _data, _updated_at
    async with _lock:
        try:
            movers_task = asyncio.to_thread(_fetch_movers_and_sectors)
            news_task = asyncio.to_thread(_fetch_headlines)

            movers_result, headlines = await asyncio.gather(
                movers_task, news_task,
                return_exceptions=True,
            )

            if not isinstance(movers_result, Exception):
                _data["top_losers"] = movers_result.get("losers", [])
                _data["top_gainers"] = movers_result.get("gainers", [])
                _data["sector_perf"] = movers_result.get("sector_perf", {})
            else:
                logger.warning("[MarketIntel.fast] movers failed: %s", movers_result)

            if not isinstance(headlines, Exception):
                _data["headlines"] = headlines
            else:
                logger.warning("[MarketIntel.fast] headlines failed: %s", headlines)

            _data["fast_refreshed_at"] = time.time()
            _updated_at = time.time()
            logger.info(
                "[MarketIntel] fast refresh: %d losers, %d gainers, %d headlines",
                len(_data.get("top_losers") or []),
                len(_data.get("top_gainers") or []),
                len(_data.get("headlines") or []),
            )
        except Exception as e:
            logger.warning("[MarketIntel] fast refresh failed: %s", e)

    # Warm parallel fast_info movers cache (outside _lock — avoids blocking MIL writes)
    try:
        await asyncio.to_thread(get_live_movers_snapshot, False)
    except Exception as e:
        logger.debug("[MarketIntel] live movers warm: %s", e)


async def refresh_slow() -> None:
    """
    Slow refresh (every 30 min): FOMC, earnings, options flow.
    These change rarely so we don't need high frequency.
    """
    global _data, _updated_at
    async with _lock:
        try:
            fomc, earnings, options_flow = await asyncio.gather(
                asyncio.to_thread(_fetch_fomc_calendar),
                asyncio.to_thread(_fetch_earnings_pulse, _EARNINGS_UNIVERSE),
                asyncio.to_thread(_fetch_options_flow, "SPY"),
                return_exceptions=True,
            )

            if not isinstance(fomc, Exception):
                _data["fomc"] = fomc
            if not isinstance(earnings, Exception):
                _data["earnings"] = earnings
            if not isinstance(options_flow, Exception):
                _data["options_flow"] = options_flow

            _data["slow_refreshed_at"] = time.time()
            _updated_at = time.time()
            logger.info(
                "[MarketIntel] slow refresh: fomc=%s, earnings=%d tickers, pcr=%s",
                (_data.get("fomc") or {}).get("next_meeting"),
                len(_data.get("earnings") or {}),
                (_data.get("options_flow") or {}).get("spy_put_call_ratio"),
            )
        except Exception as e:
            logger.warning("[MarketIntel] slow refresh failed: %s", e)


async def refresh() -> None:
    """Full refresh: both fast and slow layers. Used at startup."""
    await asyncio.gather(refresh_fast(), refresh_slow(), return_exceptions=True)


# ── Fast-layer fetchers ───────────────────────────────────────────────────────

def _fetch_movers_and_sectors() -> Dict[str, Any]:
    """
    Scan the full S&P 500 + sector ETFs using yfinance batch download.
    yf.download() fetches all tickers in a single HTTP request (~15-30s for 500).
    Returns top losers, top gainers (each top-25), and sector_perf dict.
    """
    import yfinance as yf

    sp500 = _get_sp500_universe()
    sector_syms = list(_SECTOR_ETFS.keys())
    all_syms = list(set(sp500 + sector_syms))

    try:
        # Single batch request
        raw = yf.download(
            tickers=all_syms,
            period="60d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        close = raw.get("Close")
        volume = raw.get("Volume")
        if close is None or close.shape[0] < 2:
            raise ValueError("Not enough rows in batch download")

        prev_close = close.iloc[-2]
        last_close = close.iloc[-1]
        pct_changes = ((last_close - prev_close) / prev_close * 100).dropna()

        # Compute daily returns for standard deviation and mean over the last 60 days
        daily_returns = close.pct_change() * 100
        mean_ret = daily_returns.mean()
        std_ret = daily_returns.std()
        
        # Compute average volume
        mean_vol = volume.mean() if volume is not None else None
        last_vol = volume.iloc[-1] if volume is not None else None

        movers: List[Dict[str, Any]] = []
        sector_perf: Dict[str, Any] = {}

        for sym, pct in pct_changes.items():
            price = float(last_close.get(sym, 0))
            if price <= 0:
                continue
            
            if sym in _SECTOR_ETFS:
                sector_perf[sym] = {
                    "sym": sym,
                    "name": _SECTOR_ETFS[sym],
                    "price": round(price, 2),
                    "pct": round(float(pct), 2),
                }
            else:
                # Z-score of daily returns over 60 days
                z_val = 0.0
                sym_std = std_ret.get(sym) if std_ret is not None else None
                sym_mean = mean_ret.get(sym) if mean_ret is not None else None
                sym_daily_ret = daily_returns.iloc[-1].get(sym) if not daily_returns.empty else None
                if sym_std and sym_std > 0 and sym_mean is not None and sym_daily_ret is not None:
                    z_val = (sym_daily_ret - sym_mean) / sym_std
                
                # Relative volume (volume of last day / mean volume of last 60 days)
                rel_vol = 1.0
                sym_mean_vol = mean_vol.get(sym) if mean_vol is not None else None
                sym_last_vol = last_vol.get(sym) if last_vol is not None else None
                if sym_mean_vol and sym_mean_vol > 0 and sym_last_vol is not None:
                    rel_vol = sym_last_vol / sym_mean_vol
                
                vol_val = int(sym_last_vol) if sym_last_vol is not None else 0
                
                entry = {
                    "sym": sym,
                    "price": round(price, 2),
                    "pct": round(float(pct), 2),
                    "volume": vol_val,
                    "relative_volume": round(float(rel_vol), 4),
                    "return_zscore_60d": round(float(z_val), 4),
                }
                movers.append(entry)

        movers.sort(key=lambda x: x["pct"])
        
        # Concurrently fetch news headlines for the top 5 losers and top 5 gainers
        extreme_movers = movers[:5] + list(reversed(movers))[:5]
        extreme_syms = list({m["sym"] for m in extreme_movers})
        
        news_map = {}
        if extreme_syms:
            def _get_ticker_news(sym: str) -> tuple[str, Dict[str, Any]]:
                try:
                    import yfinance as yf
                    ticker = yf.Ticker(sym)
                    news = ticker.news or []
                    if news:
                        first_article = news[0]
                        title = first_article.get("title", "")
                        title_lower = title.lower()
                        catalyst_keywords = (
                            "earnings", "dividend", "acquisition", "merger", "fda", "buyback",
                            "downgrade", "upgrade", "probe", "fraud", "miss", "beat", "ceo", "lawsuit", "guidance",
                            "regulatory", "investigation", "layoff", "restructur", "bankrupt"
                        )
                        has_cat = any(kw in title_lower for kw in catalyst_keywords)
                        category = "none"
                        if "earnings" in title_lower or "eps" in title_lower or "beat" in title_lower or "miss" in title_lower:
                            category = "earnings"
                        elif "dividend" in title_lower or "buyback" in title_lower:
                            category = "corporate_action"
                        elif has_cat:
                            category = "news"
                        
                        return sym, {
                            "catalyst_status": "symbol_specific" if has_cat else "no_catalyst",
                            "primary_cause_category": category,
                            "primary_cause_headline": title,
                            "primary_cause_weight": 0.8 if has_cat else 0.0
                        }
                except Exception:
                    pass
                return sym, {
                    "catalyst_status": "no_catalyst",
                    "primary_cause_category": "none",
                    "primary_cause_headline": "",
                    "primary_cause_weight": 0.0
                }
                
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_sym = {executor.submit(_get_ticker_news, sym): sym for sym in extreme_syms}
                for future in as_completed(future_to_sym):
                    try:
                        sym, cat_info = future.result()
                        news_map[sym] = cat_info
                    except Exception:
                        pass

        # Update movers with catalyst info and default regime/other fields
        for m in movers:
            sym = m["sym"]
            cat_info = news_map.get(sym, {
                "catalyst_status": "no_catalyst",
                "primary_cause_category": "none",
                "primary_cause_headline": "",
                "primary_cause_weight": 0.0
            })
            m.update(cat_info)
            m.setdefault("market_regime", "Balanced")

        logger.info("[MarketIntel] batch movers: %d tickers, %d sectors", len(movers), len(sector_perf))
        return {
            "losers": movers[:25],
            "gainers": list(reversed(movers))[:25],
            "sector_perf": sector_perf,
        }

    except Exception as e:
        logger.warning("[MarketIntel] batch download failed (%s), falling back to fast_info", e)
        movers = []
        sector_perf = {}
        for sym in sp500[:100] + sector_syms:
            try:
                fi = yf.Ticker(sym).fast_info
                price = fi.get("lastPrice")
                prev = fi.get("previousClose")
                if price and prev and prev > 0:
                    pct = (price - prev) / prev * 100
                    entry = {
                        "sym": sym,
                        "price": round(price, 2),
                        "pct": round(pct, 2),
                        "volume": 0,
                        "relative_volume": 1.0,
                        "return_zscore_60d": 0.0,
                        "catalyst_status": "no_catalyst",
                        "primary_cause_category": "none",
                        "primary_cause_headline": "",
                        "primary_cause_weight": 0.0,
                        "market_regime": "Balanced",
                    }
                    if sym in _SECTOR_ETFS:
                        sector_perf[sym] = {"sym": sym, "name": _SECTOR_ETFS[sym],
                                            "price": round(price, 2), "pct": round(pct, 2)}
                    else:
                        movers.append(entry)
            except Exception:
                continue
        movers.sort(key=lambda x: x["pct"])
        return {
            "losers": movers[:25],
            "gainers": list(reversed(movers))[:25],
            "sector_perf": sector_perf,
        }


def _fetch_headlines() -> List[str]:
    """
    Fetch live financial headlines from Yahoo Finance RSS + yfinance news.
    Returns a deduped list of headline strings.
    """
    import urllib.request
    import defusedxml.ElementTree as ET
    import yfinance as yf

    headlines: List[str] = []
    seen: set = set()

    # 1. Yahoo Finance RSS feeds
    rss_urls = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EJNX&region=US&lang=en-US",
    ]
    for url in rss_urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                tree = ET.parse(r)
                for item in tree.findall(".//item")[:10]:
                    title = item.findtext("title", "").strip()
                    if title and title not in seen:
                        seen.add(title)
                        headlines.append(title)
        except Exception:
            pass

    # 2. yfinance news for ^GSPC and SPY
    for sym in ("^GSPC", "SPY"):
        try:
            news = yf.Ticker(sym).news or []
            for n in news[:10]:
                t = n.get("title", "").strip()
                if t and t not in seen:
                    seen.add(t)
                    headlines.append(t)
        except Exception:
            pass

    return headlines[:30]


# ── Slow-layer fetchers ───────────────────────────────────────────────────────

def _fetch_fomc_calendar() -> Dict[str, Any]:
    try:
        import urllib.request, re
        url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")

        matches = re.findall(r"(\w+ \d{1,2}[-–]\d{1,2},\s*202[5-9])", html)
        matches += re.findall(r"(\w+ \d{1,2},\s*202[5-9])", html)
        today = datetime.now(timezone.utc).date()
        future_dates = []
        for m in matches:
            try:
                clean = m.replace("–", "-").split("-")[0].strip()
                parts = clean.split(",")
                date_str = parts[0].strip() + "," + parts[1].strip() if len(parts) > 1 else clean
                dt = datetime.strptime(date_str.strip(), "%B %d, %Y").date()
                if dt >= today:
                    future_dates.append(dt)
            except Exception:
                continue
        if future_dates:
            future_dates.sort()
            next_dt = future_dates[0]
            return {
                "next_meeting": str(next_dt),
                "days_until": (next_dt - today).days,
                "last_decision": _get_fed_rate(),
                "source": "federalreserve.gov",
            }
    except Exception as e:
        logger.debug("[MarketIntel] FOMC web fetch failed: %s", e)

    # Fallback
    today = datetime.now(timezone.utc).date()
    future = sorted(d for d in _FOMC_DATES_FALLBACK
                    if datetime.strptime(d, "%Y-%m-%d").date() >= today)
    if not future:
        return {"next_meeting": "unknown", "days_until": None, "last_decision": "unknown"}
    next_dt = datetime.strptime(future[0], "%Y-%m-%d").date()
    return {
        "next_meeting": str(next_dt),
        "days_until": (next_dt - today).days,
        "last_decision": _get_fed_rate(),
        "source": "fallback",
    }


def _get_fed_rate() -> str:
    try:
        import yfinance as yf
        irx = yf.Ticker("^IRX").fast_info.get("lastPrice")
        if irx:
            return f"3M T-Bill yield {irx:.2f}% (Fed Funds proxy)"
    except Exception:
        pass
    return "Fed Funds Rate ~4.25–4.50%"


def _fetch_earnings_pulse(tickers: List[str]) -> Dict[str, Any]:
    import yfinance as yf
    today = datetime.now(timezone.utc).date()
    result: Dict[str, Any] = {}
    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            cal = t.calendar
            next_date: Optional[str] = None
            days_until: Optional[int] = None
            if cal is not None:
                ed = cal.get("Earnings Date") if hasattr(cal, "get") else None
                if ed is not None:
                    if hasattr(ed, "__iter__") and not isinstance(ed, str):
                        ed = list(ed)
                        ed = ed[0] if ed else None
                    if ed is not None:
                        try:
                            d = ed.date() if hasattr(ed, "date") else datetime.strptime(str(ed)[:10], "%Y-%m-%d").date()
                            next_date = str(d)
                            days_until = (d - today).days
                        except Exception:
                            pass

            surprise_pct: Optional[str] = None
            trend = "unknown"
            try:
                eh = t.earnings_history
                if eh is not None and not eh.empty:
                    last = eh.iloc[-1]
                    rep = float(last.get("epsActual") if hasattr(last, "get") else last["epsActual"] or 0)
                    est = float(last.get("epsEstimate") if hasattr(last, "get") else last["epsEstimate"] or 0)
                    if est != 0:
                        pct = (rep - est) / abs(est) * 100
                        surprise_pct = f"{pct:+.1f}%"
                        trend = ("strong_beat" if pct > 5 else "beat" if pct > 0
                                 else "strong_miss" if pct < -5 else "miss")
            except Exception:
                pass

            result[sym] = {
                "next_earnings": next_date,
                "days_until": days_until,
                "last_surprise_pct": surprise_pct,
                "trend": trend,
            }
        except Exception as e:
            logger.debug("[MarketIntel] earnings failed for %s: %s", sym, e)
            result[sym] = {"next_earnings": None, "days_until": None, "last_surprise_pct": None, "trend": "unknown"}
    return result


def _fetch_options_flow(ticker: str = "SPY") -> Dict[str, Any]:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            return {"error": "no options data"}
        chain = t.option_chain(expiries[0])
        calls_vol = float(chain.calls["volume"].sum())
        puts_vol = float(chain.puts["volume"].sum())
        if calls_vol <= 0:
            return {"error": "zero call volume"}
        pcr = round(puts_vol / calls_vol, 3)
        if pcr >= 1.5:
            signal, desc = "EXTREME_FEAR", f"{pcr:.2f} puts/call — extreme bearish hedging."
        elif pcr >= 1.2:
            signal, desc = "BEARISH_FLOW", f"{pcr:.2f} puts/call — elevated fear."
        elif pcr >= 0.9:
            signal, desc = "NEUTRAL", f"{pcr:.2f} puts/call — balanced sentiment."
        elif pcr >= 0.7:
            signal, desc = "BULLISH_FLOW", f"{pcr:.2f} puts/call — options traders leaning bullish."
        else:
            signal, desc = "EXTREME_GREED", f"{pcr:.2f} puts/call — very low fear / high risk appetite."
        return {
            "spy_put_call_ratio": pcr,
            "calls_volume": int(calls_vol),
            "puts_volume": int(puts_vol),
            "signal": signal,
            "description": desc,
            "expiry_used": expiries[0],
        }
    except Exception as e:
        logger.debug("[MarketIntel] options flow failed: %s", e)
        return {"error": str(e)}
