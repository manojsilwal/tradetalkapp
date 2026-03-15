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

# Curated liquid S&P 500 universe — large enough to be meaningful,
# small enough to avoid yFinance rate limits
SP500_UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "JNJ", "V",
    "PG", "UNH", "HD", "MA", "BAC", "ABBV", "PFE", "AVGO", "KO", "PEP",
    "COST", "MRK", "TMO", "WMT", "CSCO", "ABT", "ACN", "CVX", "LLY", "MCD",
    "DHR", "NEE", "NKE", "TXN", "AMD", "PM", "ORCL", "IBM", "CRM", "QCOM",
    "HON", "AMGN", "LIN", "SBUX", "INTU", "GS", "BLK", "SPGI", "CAT", "BA",
    "AXP", "MS", "RTX", "ISRG", "ADI", "MDLZ", "GILD", "TJX", "BKNG", "NOW",
    "DE", "MMM", "SYK", "ZTS", "CI", "USB", "MO", "REGN", "VRTX", "HCA",
    "EOG", "SLB", "PSA", "WELL", "DUK", "SO", "EXC", "D", "AEP", "XEL",
    "APD", "SHW", "PPG", "ECL", "EMR", "ITW", "GE", "ETN", "PH", "ROK",
    "F", "GM", "UBER", "LYFT", "ABNB", "DASH", "SNAP", "PINS", "TWTR", "ZM",
    "PYPL", "SQ", "SHOP", "ROKU", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
    "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "AVB", "EQR", "MAA", "UDR",
    "XOM", "CVX", "COP", "MPC", "PSX", "VLO", "OXY", "HES", "DVN", "FANG",
    "WFC", "C", "PNC", "TFC", "STT", "BK", "COF", "AIG", "MET", "PRU",
]

# ── SEC EDGAR helpers ─────────────────────────────────────────────────────────
# Public API, no key required.
# Rate limit: 10 req/sec — we stay well under with 0.15s sleeps.
# User-Agent is required by SEC fair-use policy.

_EDGAR_USER_AGENT = "TradeTalk Backtest contact@tradetalk.app"

# In-memory caches — persist for the lifetime of the process
_CIK_MAP: dict   = {}   # ticker.upper() → zero-padded 10-digit CIK string  (or None)
_CIK_MAP_LOADED  = threading.Event()
_EDGAR_EPS_CACHE: dict = {}   # ticker.upper() → list[{date, eps}]
_EDGAR_CACHE_LOCK = threading.Lock()


def _load_cik_map() -> None:
    """
    Lazy-load the full SEC company_tickers.json (once per process).
    Maps every known US-listed ticker to its 10-digit CIK string.
    """
    if _CIK_MAP_LOADED.is_set():
        return
    try:
        import requests
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": _EDGAR_USER_AGENT},
            timeout=20,
        )
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

    Returns a list of {date: str, eps: float} sorted ascending by date.
    Data typically goes back to 2009–2010 for large-cap US stocks.

    Results are cached in-memory for the lifetime of the process — each
    ticker is only fetched once regardless of how many backtests run.
    """
    t = ticker.upper()

    # Return cached result immediately (even if empty)
    with _EDGAR_CACHE_LOCK:
        if t in _EDGAR_EPS_CACHE:
            return _EDGAR_EPS_CACHE[t]

    result: list = []
    try:
        import requests

        cik = _ticker_to_cik(t)
        if not cik:
            logger.debug(f"[EDGAR] No CIK found for {t}")
            with _EDGAR_CACHE_LOCK:
                _EDGAR_EPS_CACHE[t] = result
            return result

        # Polite rate-limiting — stay well under SEC's 10 req/sec cap
        time.sleep(0.15)

        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        r = requests.get(url, headers={"User-Agent": _EDGAR_USER_AGENT}, timeout=30)
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

    Batches in groups of 20 to avoid yFinance rate limits.
    SEC EDGAR EPS is fetched per-ticker inside _fetch_one and cached.
    """
    batch_size = 20
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
            await asyncio.sleep(0.5)
    return results


def _fetch_one(ticker: str, start: str, end: str) -> dict:
    try:
        import yfinance as yf
        import pandas as pd

        t = yf.Ticker(ticker.upper())
        hist = t.history(start=start, end=end, auto_adjust=True)

        prices = []
        if not hist.empty:
            for date_idx, row in hist.iterrows():
                prices.append({
                    "date":   str(date_idx.date()),
                    "open":   round(float(row["Open"]),   4),
                    "high":   round(float(row["High"]),   4),
                    "low":    round(float(row["Low"]),    4),
                    "close":  round(float(row["Close"]),  4),
                    "volume": int(row["Volume"]),
                })

        info = t.info or {}

        # ── Annual financials (last 4 years from yFinance) ────────────────
        annual_financials: dict = {}
        try:
            fin = t.financials
            if fin is not None and not fin.empty:
                for col in fin.columns[:4]:
                    year_str = str(col.year) if hasattr(col, "year") else str(col)[:4]
                    annual_financials[year_str] = {
                        "total_revenue": _safe_float(fin.get("Total Revenue", {}).get(col)),
                        "net_income":    _safe_float(fin.get("Net Income",     {}).get(col)),
                        "gross_profit":  _safe_float(fin.get("Gross Profit",   {}).get(col)),
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
                    annual_financials[year_str]["total_debt"] = _safe_float(
                        bs.get("Total Debt", bs.get("Long Term Debt", {})).get(col)
                    )
                    annual_financials[year_str]["cash"] = _safe_float(
                        bs.get("Cash And Cash Equivalents", {}).get(col)
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


def resolve_universe(universe_hint: str, tickers: list = None) -> list:
    """Resolve a universe hint to a concrete list of tickers."""
    if tickers and len(tickers) > 0:
        return [t.upper() for t in tickers]
    hint = (universe_hint or "").lower()
    if any(k in hint for k in ("mag7", "magnificent 7", "magnificent seven", "mag 7")):
        return MAG7_UNIVERSE
    return SP500_UNIVERSE
