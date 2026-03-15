"""
Backtest Data Connector — fetches historical OHLC price data, fundamentals, and
quarterly EPS for PE computation over a date range.

Data sources and depth:
  - Price history  : yFinance  — 20+ years of OHLC
  - Quarterly EPS  : yFinance  ~5–8 years (primary)
                     SEC EDGAR ~15 years going back to 2010 (augment/fallback)
  - Annual fins    : yFinance  — last 4 reported years

Combined, PE-based strategies now work reliably from 2010 onward.
"""
import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Magnificent 7 — explicit small universe for PE-based strategies
MAG7_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "TSLA"]

# Curated 40-stock liquid universe — covers all major sectors, keeps cloud
# requests low enough to avoid Yahoo Finance rate limits on server IPs.
# (The full 100+ ticker list caused too many 401/429 errors on Render.)
SP500_UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "JNJ", "V",
    "PG",   "HD",   "MA",   "KO",   "PEP",  "AVGO", "WMT",  "MCD", "ABBV", "TMO",
    "UNH",  "CVX",  "XOM",  "LLY",  "NFLX", "COST", "CSCO", "TXN", "MRK",  "NKE",
    "BAC",  "SBUX", "AMGN", "GS",   "QCOM", "AMD",  "INTU", "SPGI","BLK",  "DIS",
]

# ── SEC EDGAR helpers ─────────────────────────────────────────────────────────
# Public API, no key required.
# Fair-use rules: max 10 req/sec; User-Agent header required.
# We target 8 req/sec (20% headroom) using a GLOBAL sliding-window rate limiter
# shared across ALL threads — a simple per-thread sleep is NOT sufficient when
# multiple tickers are fetched concurrently.

import os
_EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT",
    "TradeTalk Backtest contact@tradetalk.app",
)

# ── Global rate limiter (max 8 EDGAR HTTP requests per second) ───────────────
# Uses a serialising lock + minimum inter-request interval so that parallel
# threads collectively never exceed the SEC limit.
_EDGAR_RATE_LOCK         = threading.Lock()
_EDGAR_LAST_REQUEST_AT   = 0.0          # epoch seconds of most recent request
_EDGAR_MIN_INTERVAL      = 1.0 / 8     # 8 req/sec → 0.125 s between requests


def _edgar_get(url: str, timeout: int = 30):
    """
    Make a rate-limited GET request to SEC EDGAR.

    Acquires _EDGAR_RATE_LOCK, waits if needed so at most 8 requests/sec are
    sent across all threads, then releases the lock BEFORE the actual network
    call so other threads can queue while this one is in-flight.
    """
    import requests as _req

    global _EDGAR_LAST_REQUEST_AT

    with _EDGAR_RATE_LOCK:
        now  = time.monotonic()
        wait = _EDGAR_MIN_INTERVAL - (now - _EDGAR_LAST_REQUEST_AT)
        if wait > 0:
            time.sleep(wait)
        _EDGAR_LAST_REQUEST_AT = time.monotonic()
        # Lock released here — HTTP call happens outside the critical section

    return _req.get(url, headers={"User-Agent": _EDGAR_USER_AGENT}, timeout=timeout)


# In-memory caches — persist for the lifetime of the process
_CIK_MAP: dict        = {}   # ticker.upper() → zero-padded 10-digit CIK  (or None)
_CIK_MAP_LOADED       = threading.Event()
_CIK_MAP_INIT_LOCK    = threading.Lock()   # ensures _load_cik_map runs only once
_EDGAR_EPS_CACHE: dict = {}  # ticker.upper() → list[{date, eps}]
_EDGAR_CACHE_LOCK      = threading.Lock()


def _load_cik_map() -> None:
    """
    Lazy-load the full SEC company_tickers.json (exactly once per process).
    Maps every known US-listed ticker to its 10-digit CIK string.
    """
    if _CIK_MAP_LOADED.is_set():
        return
    with _CIK_MAP_INIT_LOCK:
        if _CIK_MAP_LOADED.is_set():   # double-checked locking
            return
        try:
            r = _edgar_get("https://www.sec.gov/files/company_tickers.json", timeout=20)
            r.raise_for_status()
            for entry in r.json().values():
                ticker = str(entry.get("ticker", "")).upper()
                cik    = str(entry.get("cik_str", "")).zfill(10)
                if ticker and cik:
                    _CIK_MAP[ticker] = cik
            logger.info(f"[EDGAR] CIK map loaded: {len(_CIK_MAP)} tickers")
        except Exception as e:
            logger.warning(f"[EDGAR] CIK map load failed: {e}")
        finally:
            _CIK_MAP_LOADED.set()


def _ticker_to_cik(ticker: str) -> Optional[str]:
    _load_cik_map()
    return _CIK_MAP.get(ticker.upper())


def _fetch_edgar_quarterly_eps(ticker: str) -> list:
    """
    Fetch all quarterly EPS filings for `ticker` from SEC EDGAR XBRL API.

    Returns list[{date: str, eps: float}] sorted ascending by date.
    Data typically goes back to 2009–2010 for large-cap US stocks.

    Results are cached in-memory (per process) — each ticker fetched once only.
    Rate-limited to ≤8 req/sec across all concurrent threads via _edgar_get().
    """
    t = ticker.upper()

    # Fast path — already cached (hit or miss)
    with _EDGAR_CACHE_LOCK:
        if t in _EDGAR_EPS_CACHE:
            return _EDGAR_EPS_CACHE[t]

    result: list = []
    try:
        cik = _ticker_to_cik(t)
        if not cik:
            logger.debug(f"[EDGAR] No CIK found for {t}")
            with _EDGAR_CACHE_LOCK:
                _EDGAR_EPS_CACHE[t] = result
            return result

        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        r = _edgar_get(url)          # rate-limited; raises on HTTP error
        r.raise_for_status()

        facts   = r.json().get("facts", {})
        us_gaap = facts.get("us-gaap", {})

        # Try diluted EPS first, then basic
        eps_entries: Optional[list] = None
        for concept in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
            node = us_gaap.get(concept, {}).get("units", {})
            # Unit key is "USD/shares" in most filings, occasionally "USD"
            for unit_key in ("USD/shares", "USD"):
                if unit_key in node:
                    eps_entries = node[unit_key]
                    break
            if eps_entries:
                break

        if not eps_entries:
            logger.debug(f"[EDGAR] No EPS concept found for {t}")
            with _EDGAR_CACHE_LOCK:
                _EDGAR_EPS_CACHE[t] = result
            return result

        # Keep only individual-quarter entries (fp = Q1/Q2/Q3/Q4).
        # Annual (fp=FY) and trailing-twelve-month frames are excluded
        # because we do our own TTM aggregation in the engine.
        seen: set = set()
        for entry in eps_entries:
            fp   = entry.get("fp", "")
            form = entry.get("form", "")
            end  = entry.get("end", "")
            val  = entry.get("val")

            if val is None or not end or len(end) < 10:
                continue
            if fp not in ("Q1", "Q2", "Q3", "Q4"):
                continue
            if form not in ("10-Q", "10-K"):
                continue

            date_key = end[:10]
            if date_key in seen:
                continue
            seen.add(date_key)
            result.append({"date": date_key, "eps": float(val)})

        result.sort(key=lambda x: x["date"])
        logger.info(f"[EDGAR] {t}: {len(result)} quarterly EPS points from SEC EDGAR "
                    f"({result[0]['date'][:4] if result else 'N/A'} – "
                    f"{result[-1]['date'][:4] if result else 'N/A'})")

    except Exception as e:
        logger.warning(f"[EDGAR] Failed to fetch EPS for {t}: {e}")

    with _EDGAR_CACHE_LOCK:
        _EDGAR_EPS_CACHE[t] = result
    return result


# ── Main data fetching ────────────────────────────────────────────────────────

async def fetch_backtest_data(tickers: list, start: str, end: str) -> dict:
    """
    Fetch historical price data and fundamentals for all tickers.
    Returns: {ticker: {prices, annual_financials, info, quarterly_eps}}

    Batches in groups of 10 (reduced from 20) to stay below Yahoo Finance's
    cloud-IP rate limits. 1.5s pause between batches for the same reason.
    """
    batch_size = 10   # conservative for cloud IPs
    results = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i: i + batch_size]
        tasks = [asyncio.to_thread(_fetch_one, t, start, end) for t in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for ticker, result in zip(batch, batch_results):
            if isinstance(result, Exception):
                logger.warning(f"[BacktestData] Failed for {ticker}: {result}")
                results[ticker] = {"prices": [], "annual_financials": {}, "info": {}, "quarterly_eps": []}
            else:
                results[ticker] = result
        if i + batch_size < len(tickers):
            await asyncio.sleep(1.5)   # longer pause — cloud IPs get throttled faster
    return results


def _fetch_one(ticker: str, start: str, end: str) -> dict:
    try:
        import yfinance as yf

        # DO NOT pass a custom session — yFinance 1.x uses curl_cffi internally
        # for cookie/crumb management. Passing a requests.Session breaks it.
        t = yf.Ticker(ticker.upper())
        hist = t.history(start=start, end=end, auto_adjust=True)

        prices = []
        if hist is not None and not hist.empty:
            for date_idx, row in hist.iterrows():
                try:
                    prices.append({
                        "date":   str(date_idx.date()),
                        "open":   round(float(row["Open"]),   4),
                        "high":   round(float(row["High"]),   4),
                        "low":    round(float(row["Low"]),    4),
                        "close":  round(float(row["Close"]),  4),
                        "volume": int(row.get("Volume", 0) or 0),
                    })
                except Exception:
                    continue

        info = {}
        try:
            raw_info = t.info
            info = raw_info if isinstance(raw_info, dict) else {}
        except Exception:
            pass

        # ── Annual financials (last 4 years from yFinance) ────────────────
        annual_financials: dict = {}
        try:
            fin = t.income_stmt   # yFinance 1.x — t.financials is deprecated
            if fin is None or fin.empty:
                fin = t.financials  # fallback for older versions
            if fin is not None and not fin.empty:
                for col in fin.columns[:4]:
                    year_str = str(col.year) if hasattr(col, "year") else str(col)[:4]
                    annual_financials[year_str] = {
                        "total_revenue": _safe_float(_df_get(fin, "Total Revenue", col)),
                        "net_income":    _safe_float(_df_get(fin, "Net Income",     col)),
                        "gross_profit":  _safe_float(_df_get(fin, "Gross Profit",   col)),
                    }
        except Exception:
            pass

        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                for col in bs.columns[:4]:
                    year_str = str(col.year) if hasattr(col, "year") else str(col)[:4]
                    if year_str not in annual_financials:
                        annual_financials[year_str] = {}
                    # Use _df_get to safely look up rows by label (index) not column
                    debt = _df_get(bs, "Total Debt", col) or _df_get(bs, "Long Term Debt", col)
                    annual_financials[year_str]["total_debt"] = _safe_float(debt)
                    annual_financials[year_str]["cash"] = _safe_float(
                        _df_get(bs, "Cash And Cash Equivalents", col)
                    )
        except Exception:
            pass

        # ── Quarterly EPS: yFinance primary (~5-8 yr), SEC EDGAR fallback (15 yr) ──
        quarterly_eps: list = []
        try:
            qis = t.quarterly_income_stmt
            if qis is not None and not qis.empty:
                eps_row = None
                for row_name in ("Diluted EPS", "Basic EPS"):
                    if row_name in qis.index:
                        eps_row = qis.loc[row_name]
                        break
                if eps_row is not None:
                    for col in qis.columns:
                        v = _safe_float(eps_row.get(col))
                        if v is not None:
                            d = col.date() if hasattr(col, "date") else col
                            quarterly_eps.append({"date": str(d)[:10], "eps": v})
                else:
                    shares = _safe_float((info or {}).get("sharesOutstanding"))
                    if shares and shares > 0 and "Net Income" in qis.index:
                        ni_row = qis.loc["Net Income"]
                        for col in qis.columns:
                            ni = _safe_float(ni_row.get(col))
                            if ni is not None:
                                d = col.date() if hasattr(col, "date") else col
                                quarterly_eps.append({"date": str(d)[:10], "eps": ni / shares})
        except Exception:
            pass

        if not quarterly_eps:
            try:
                qe = t.quarterly_earnings
                if qe is not None and not qe.empty:
                    col_name = next(
                        (c for c in ("Earnings", "EPS") if c in qe.columns),
                        qe.columns[0] if len(qe.columns) else None
                    )
                    if col_name:
                        for date_idx, row in qe.iterrows():
                            v = _safe_float(row.get(col_name))
                            if v is not None:
                                d = date_idx.date() if hasattr(date_idx, "date") else date_idx
                                quarterly_eps.append({"date": str(d)[:10], "eps": v})
            except Exception:
                pass

        # ── Augment with SEC EDGAR historical EPS (fills in pre-2018 quarters) ──
        try:
            edgar_eps = _fetch_edgar_quarterly_eps(ticker)
            if edgar_eps:
                yf_dates = {q["date"] for q in quarterly_eps}
                older = [q for q in edgar_eps if q["date"] not in yf_dates]
                if older:
                    quarterly_eps = sorted(older + quarterly_eps, key=lambda x: x["date"])
                    logger.debug(f"[BacktestData] {ticker}: merged {len(older)} older EDGAR EPS pts "
                                 f"(total: {len(quarterly_eps)})")
        except Exception as e:
            logger.debug(f"[BacktestData] EDGAR merge skipped for {ticker}: {e}")

        quarterly_eps.sort(key=lambda x: x["date"])
        logger.debug(f"[BacktestData] {ticker}: {len(quarterly_eps)} total EPS pts | "
                     f"range: {quarterly_eps[0]['date'][:7] if quarterly_eps else 'N/A'} – "
                     f"{quarterly_eps[-1]['date'][:7] if quarterly_eps else 'N/A'}")

        return {
            "prices":             prices,
            "annual_financials":  annual_financials,
            "info":               info,
            "quarterly_eps":      quarterly_eps,
        }
    except Exception as e:
        logger.warning(f"[BacktestData] Error fetching {ticker}: {e}")
        return {"prices": [], "annual_financials": {}, "info": {}, "quarterly_eps": []}


def _safe_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        import math
        f = float(val)
        return None if math.isnan(f) else round(f, 2)
    except Exception:
        return None


def _df_get(df, row_label: str, col):
    """
    Safely retrieve df.loc[row_label, col] from a yFinance DataFrame.
    yFinance DataFrames have financial metrics as the ROW index and dates
    as COLUMNS — so we must use .loc[], not .get() (which searches columns).
    Returns None on any error.
    """
    try:
        if df is None or df.empty:
            return None
        if row_label not in df.index:
            return None
        val = df.loc[row_label, col]
        return None if val is None else val
    except Exception:
        return None


def resolve_universe(universe_hint: str, tickers: list = None) -> list:
    """Resolve a universe hint to a concrete list of tickers."""
    if tickers and len(tickers) > 0:
        return [t.upper() for t in tickers]
    hint = (universe_hint or "").lower()
    if any(k in hint for k in ("mag7", "magnificent 7", "magnificent seven", "mag 7")):
        return MAG7_UNIVERSE
    return SP500_UNIVERSE
