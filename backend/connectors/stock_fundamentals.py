"""
Stock Fundamentals Connector — consolidated data for the stock analysis page.

Fetches price history, valuation metrics, financial performance, and company
info from yfinance with fallbacks:
  - Spot price: ``resolve_spot`` (Yahoo chart → Stooq → FinCrawler → yfinance)
  - Thin/missing metrics: FinCrawler ``/quote/smart`` when configured
  - Empty chart bars: Yahoo chart API (same surface as quote fallbacks)

Follows the truthful-data contract: unavailable fields are ``None``, never
fabricated. Partial payloads are returned when at least price or core metrics
are available; the router rejects only truly empty responses.
"""
from __future__ import annotations

import json
import logging
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Period → interval mapping for price history chart
# ---------------------------------------------------------------------------
_PERIOD_INTERVAL_MAP: Dict[str, str] = {
    "1d": "5m",
    "5d": "15m",
    "1mo": "1d",
    "6mo": "1d",
    "ytd": "1d",
    "1y": "1d",
    "5y": "1wk",
    "max": "1mo",
}

# Exchange code → human-readable name
_EXCHANGE_MAP: Dict[str, str] = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYQ": "NYSE",
    "NYS": "NYSE",
    "PCX": "NYSE ARCA",
    "ASE": "NYSE AMEX",
    "BTS": "CBOE BZX",
    "LSE": "London",
    "TYO": "Tokyo",
    "HKG": "Hong Kong",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    """Safe numeric conversion; returns *default* on failure or NaN."""
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_div(
    numerator: Optional[float],
    denominator: Optional[float],
    default: Optional[float] = None,
) -> Optional[float]:
    """Safe division; returns *default* when either operand is None/zero."""
    if numerator is None or denominator is None or denominator == 0:
        return default
    return numerator / denominator


def _safe_yfinance_info(ticker_obj: Any) -> Tuple[Dict[str, Any], bool]:
    """Return (info dict, degraded) — never raises on yfinance parse failures."""
    try:
        raw = ticker_obj.info
        if isinstance(raw, dict):
            return raw, False
        return {}, True
    except Exception as exc:
        logger.warning("yfinance .info failed: %s", exc)
        return {}, True


def _fetch_fc_fundamentals_sync(ticker: str) -> Dict[str, Any]:
    try:
        from backend.fincrawler_client import fc

        return fc.get_fundamentals_sync(ticker) or {}
    except Exception as exc:
        logger.warning("FinCrawler fundamentals fallback failed for %s: %s", ticker, exc)
        return {}


def _merge_fc_into_info(info: Dict[str, Any], fc: Dict[str, Any]) -> Dict[str, Any]:
    if not fc:
        return info
    merged = dict(info)
    name = fc.get("company_name")
    if name:
        merged.setdefault("longName", name)
        merged.setdefault("shortName", name)
    if fc.get("market_cap") is not None:
        merged.setdefault("marketCap", fc["market_cap"])
    if fc.get("pe_ratio") is not None:
        merged.setdefault("trailingPE", fc["pe_ratio"])
    if fc.get("forward_pe") is not None:
        merged.setdefault("forwardPE", fc["forward_pe"])
    if fc.get("regular_market_price") is not None:
        merged.setdefault("regularMarketPrice", fc["regular_market_price"])
    if fc.get("change_pct") is not None:
        merged.setdefault("regularMarketChangePercent", fc["change_pct"])
    return merged


def _patch_metrics_from_fc(metrics: Dict[str, Any], fc: Dict[str, Any]) -> None:
    if not fc:
        return
    val = metrics.setdefault("valuation", {})
    if val.get("market_cap") is None and fc.get("market_cap") is not None:
        val["market_cap"] = _num(fc["market_cap"])
    if val.get("trailing_pe") is None and fc.get("pe_ratio") is not None:
        val["trailing_pe"] = _num(fc["pe_ratio"])
    if val.get("forward_pe") is None and fc.get("forward_pe") is not None:
        val["forward_pe"] = _num(fc["forward_pe"])


def _metrics_sparse(metrics: Dict[str, Any]) -> bool:
    val = metrics.get("valuation") or {}
    filled = sum(
        1
        for key in ("market_cap", "trailing_pe", "forward_pe", "price_to_sales", "ev_to_ebitda")
        if val.get(key) is not None
    )
    return filled < 1


def _fetch_yahoo_chart_bars(symbol: str, period: str) -> List[Dict[str, Any]]:
    """OHLCV bars via Yahoo chart API when yfinance history is empty."""
    interval = _PERIOD_INTERVAL_MAP.get(period)
    if not interval:
        return []
    sym = urllib.parse.quote((symbol or "").upper().strip(), safe="")
    if not sym:
        return []
    query = urllib.parse.urlencode({"range": period, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?{query}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradeTalk/1.0 (stock-fundamentals chart fallback)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            if getattr(resp, "status", 200) == 429:
                return []
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("Yahoo chart fallback failed period=%s symbol=%s: %s", period, symbol, exc)
        return []

    results = (raw.get("chart") or {}).get("result") or []
    if not results:
        return []
    block = results[0]
    timestamps = block.get("timestamp") or []
    quotes = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    if not timestamps:
        return []

    def _series(key: str) -> List[Any]:
        return list(quotes.get(key) or [])

    opens, highs, lows, closes, volumes = (
        _series("open"),
        _series("high"),
        _series("low"),
        _series("close"),
        _series("volume"),
    )
    bars: List[Dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        try:
            close_f = float(close)
            if math.isnan(close_f):
                continue
        except (TypeError, ValueError):
            continue
        ts_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        bars.append({
            "timestamp": ts_iso,
            "open": _num(opens[i] if i < len(opens) else None),
            "high": _num(highs[i] if i < len(highs) else None),
            "low": _num(lows[i] if i < len(lows) else None),
            "close": round(close_f, 4),
            "volume": int(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
        })
    return bars


def _apply_spot_to_company_info(company_info: Dict[str, Any], ticker: str) -> None:
    from .spot import resolve_spot

    spot_q = resolve_spot(ticker)
    if spot_q is None:
        return
    company_info["current_price"] = round(spot_q.price, 4)
    company_info["spot_source"] = spot_q.source
    prev = company_info.get("previous_close")
    if prev is not None and prev != 0:
        company_info["price_change"] = round(spot_q.price - prev, 4)
        company_info["price_change_pct"] = round((spot_q.price - prev) / prev * 100, 4)


def fundamentals_payload_usable(result: Optional[dict]) -> bool:
    """True when the dashboard can show price and/or core valuation fields."""
    if not result:
        return False
    ci = result.get("company_info") or {}
    if ci.get("current_price") is not None:
        return True
    ph = result.get("price_history") or {}
    if any(isinstance(bars, list) and bars for bars in ph.values()):
        return True
    metrics = result.get("metrics") or {}
    val = metrics.get("valuation") or {}
    if any(val.get(k) is not None for k in ("market_cap", "trailing_pe", "forward_pe")):
        return True
    cf = metrics.get("cash_flow") or {}
    if cf.get("free_cash_flow") is not None or cf.get("fcf_yield") is not None:
        return True
    return False


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------
def _fetch_price_history(ticker_obj: Any, period: str) -> List[Dict[str, Any]]:
    """Return OHLCV bars for *period* using the matching interval."""
    interval = _PERIOD_INTERVAL_MAP.get(period)
    if interval is None:
        return []
    try:
        hist = ticker_obj.history(period=period, interval=interval)
        if hist is None or hist.empty:
            return []
        bars: List[Dict[str, Any]] = []
        for ts, row in hist.iterrows():
            bars.append({
                "timestamp": ts.isoformat(),
                "open": round(float(row["Open"]), 4) if not math.isnan(row["Open"]) else None,
                "high": round(float(row["High"]), 4) if not math.isnan(row["High"]) else None,
                "low": round(float(row["Low"]), 4) if not math.isnan(row["Low"]) else None,
                "close": round(float(row["Close"]), 4) if not math.isnan(row["Close"]) else None,
                "volume": int(row["Volume"]) if not math.isnan(row["Volume"]) else None,
            })
        return bars
    except Exception as exc:
        logger.warning("Price history fetch failed for period=%s: %s", period, exc)
        return []


# ---------------------------------------------------------------------------
# Consolidated metrics from .info
# ---------------------------------------------------------------------------
def _build_metrics(info: Dict[str, Any]) -> Dict[str, Any]:
    """Extract valuation, cash-flow, margins, growth, balance, and dividend metrics."""
    market_cap = _num(info.get("marketCap"))
    fcf = _num(info.get("freeCashflow"))
    shares = _num(info.get("sharesOutstanding"))
    ocf = _num(info.get("operatingCashflow"))
    capex = _num(info.get("capitalExpenditures"))
    owner_earnings = None
    if ocf is not None and capex is not None:
        owner_earnings = ocf + capex if capex <= 0 else ocf - abs(capex)

    fcf_yield = _safe_div(fcf, market_cap)
    fcf_per_share = _safe_div(fcf, shares)

    return {
        "valuation": {
            "market_cap": market_cap,
            "trailing_pe": _num(info.get("trailingPE")),
            "forward_pe": _num(info.get("forwardPE")),
            "price_to_sales": _num(info.get("priceToSalesTrailing12Months")),
            "ev_to_ebitda": _num(info.get("enterpriseToEbitda")),
        },
        "cash_flow": {
            "free_cash_flow": fcf,
            "operating_cash_flow": ocf,
            "capital_expenditures": capex,
            "owner_earnings": owner_earnings,
            "fcf_yield": round(fcf_yield, 4) if fcf_yield is not None else None,
            "fcf_per_share": round(fcf_per_share, 2) if fcf_per_share is not None else None,
            "period_label": "TTM",
        },
        "margins_and_growth": {
            "profit_margins": _num(info.get("profitMargins")),
            "operating_margins": _num(info.get("operatingMargins")),
            "earnings_growth_yoy": _num(info.get("earningsGrowth")),
            "revenue_growth_yoy": _num(info.get("revenueGrowth")),
        },
        "balance": {
            "total_cash": _num(info.get("totalCash")),
            "total_debt": _num(info.get("totalDebt")),
        },
        "dividend": {
            "dividend_yield": _num(info.get("dividendYield")),
            "payout_ratio": _num(info.get("payoutRatio")),
        },
    }


# ---------------------------------------------------------------------------
# Financial performance (income statement)
# ---------------------------------------------------------------------------
_REVENUE_LABELS = ("Total Revenue", "Revenue", "Operating Revenue")
_NET_INCOME_LABELS = ("Net Income", "Net Income Common Stockholders")


def _find_row(df: Any, labels: tuple[str, ...]) -> Any:
    """Return the first matching row from a DataFrame's index."""
    if df is None or getattr(df, "empty", True):
        return None
    index = getattr(df, "index", None)
    if index is None:
        return None
    for label in labels:
        if label in index:
            return df.loc[label]
    return None


def _extract_financials(df: Any) -> List[Dict[str, Any]]:
    """Pull revenue and net income from an income statement DataFrame."""
    revenue_row = _find_row(df, _REVENUE_LABELS)
    ni_row = _find_row(df, _NET_INCOME_LABELS)
    if revenue_row is None and ni_row is None:
        return []

    periods: List[Dict[str, Any]] = []
    # Use revenue_row columns as the canonical set; fall back to ni_row
    rev_index = getattr(revenue_row, "index", None) if revenue_row is not None else None
    ni_index = getattr(ni_row, "index", None) if ni_row is not None else None
    cols = rev_index if rev_index is not None else ni_index
    if cols is None:
        return []
    for col in cols:
        period_label = col.date().isoformat() if hasattr(col, "date") else str(col)
        rev = _num(revenue_row[col]) if revenue_row is not None and rev_index is not None and col in rev_index else None
        ni = _num(ni_row[col]) if ni_row is not None and ni_index is not None and col in ni_index else None
        periods.append({
            "period": period_label,
            "revenue": rev,
            "net_income": ni,
        })

    # Sort chronologically (oldest first)
    periods.sort(key=lambda x: x["period"])
    return periods


# ---------------------------------------------------------------------------
# Company info
# ---------------------------------------------------------------------------
def _build_company_info(info: Dict[str, Any]) -> Dict[str, Any]:
    """Extract display-friendly company identification and current price data."""
    company_name = info.get("longName") or info.get("shortName") or None

    raw_exchange = info.get("exchange") or ""
    exchange = _EXCHANGE_MAP.get(raw_exchange, raw_exchange) if raw_exchange else None

    current_price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    previous_close = _num(info.get("previousClose"))

    price_change: Optional[float] = None
    price_change_pct: Optional[float] = None
    if current_price is not None and previous_close is not None and previous_close != 0:
        price_change = round(current_price - previous_close, 4)
        price_change_pct = round((price_change / previous_close) * 100, 4)

    return {
        "company_name": company_name,
        "exchange": exchange,
        "current_price": current_price,
        "previous_close": previous_close,
        "price_change": price_change,
        "price_change_pct": price_change_pct,
    }


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------
def fetch_stock_fundamentals(ticker: str) -> dict:
    """
    Fetch consolidated stock data for the analysis page redesign.

    Returns a dict with keys:
      - ``ticker``
      - ``company_info``    — name, exchange, price, change
      - ``price_history``   — dict of period → list of OHLCV bars
      - ``metrics``         — valuation, cash-flow, margins, growth, balance, dividend
      - ``financials``      — quarterly and annual revenue + net income
      - ``market_data_degraded`` — True when fallbacks or partial yfinance were used
      - ``data_sources``    — provenance hints per section
    """
    import yfinance as yf

    t_up = ticker.upper().strip()
    data_sources: Dict[str, str] = {
        "info": "none",
        "price_history": "none",
        "metrics": "none",
    }
    degraded = False
    info: Dict[str, Any] = {}
    price_history: Dict[str, List[Dict[str, Any]]] = {
        period: [] for period in _PERIOD_INTERVAL_MAP
    }
    quarterly_financials: List[Dict[str, Any]] = []
    annual_financials: List[Dict[str, Any]] = []

    ticker_obj = None
    try:
        ticker_obj = yf.Ticker(t_up)
        info, info_degraded = _safe_yfinance_info(ticker_obj)
        degraded = degraded or info_degraded
        if info:
            data_sources["info"] = "yfinance"

        for period in _PERIOD_INTERVAL_MAP:
            bars: List[Dict[str, Any]] = []
            if ticker_obj is not None:
                bars = _fetch_price_history(ticker_obj, period)
            if bars:
                price_history[period] = bars
                if data_sources["price_history"] == "none":
                    data_sources["price_history"] = "yfinance"
            else:
                chart_bars = _fetch_yahoo_chart_bars(t_up, period)
                price_history[period] = chart_bars
                if chart_bars:
                    data_sources["price_history"] = "yahoo_chart"
                    degraded = True

        try:
            if ticker_obj is not None:
                quarterly_financials = _extract_financials(ticker_obj.quarterly_income_stmt)
        except Exception as exc:
            logger.warning("Quarterly income stmt failed for %s: %s", t_up, exc)
        try:
            if ticker_obj is not None:
                annual_financials = _extract_financials(ticker_obj.income_stmt)
        except Exception as exc:
            logger.warning("Annual income stmt failed for %s: %s", t_up, exc)
    except Exception as exc:
        logger.warning("[StockFundamentalsConnector] yfinance path failed for %s: %s", t_up, exc)
        degraded = True

    metrics = _build_metrics(info)
    fc_row: Dict[str, Any] = {}
    if _metrics_sparse(metrics):
        fc_row = _fetch_fc_fundamentals_sync(t_up)
        if fc_row:
            info = _merge_fc_into_info(info, fc_row)
            metrics = _build_metrics(info)
            _patch_metrics_from_fc(metrics, fc_row)
            data_sources["metrics"] = "fincrawler"
            degraded = True
        elif info:
            data_sources["metrics"] = "yfinance"
    else:
        data_sources["metrics"] = "yfinance"

    company_info = _build_company_info(info)
    _apply_spot_to_company_info(company_info, t_up)

    if company_info.get("current_price") is None:
        if not fc_row:
            fc_row = _fetch_fc_fundamentals_sync(t_up)
        px = _num(fc_row.get("regular_market_price"))
        if px is not None:
            company_info["current_price"] = round(px, 4)
            company_info["spot_source"] = "fincrawler"
            degraded = True

    result = {
        "ticker": t_up,
        "company_info": company_info,
        "price_history": price_history,
        "metrics": metrics,
        "financials": {
            "quarterly": quarterly_financials,
            "annual": annual_financials,
        },
        "market_data_degraded": degraded,
        "data_sources": data_sources,
    }

    if not fundamentals_payload_usable(result):
        logger.error(
            "[StockFundamentalsConnector] no usable fundamentals for %s after fallbacks",
            t_up,
        )
        raise RuntimeError(
            f"No usable price or valuation data for {t_up} after yfinance, "
            "Yahoo chart, FinCrawler, and spot fallbacks."
        )

    return result
