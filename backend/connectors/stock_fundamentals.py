"""
Stock Fundamentals Connector — consolidated data for the stock analysis page.

Fetches price history, valuation metrics, financial performance, and company
info from yfinance.  Follows the truthful-data contract: unavailable fields
are returned as ``None``, never fabricated.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

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
    for label in labels:
        if label in df.index:
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
    cols = revenue_row.index if revenue_row is not None else ni_row.index
    for col in cols:
        period_label = col.date().isoformat() if hasattr(col, "date") else str(col)
        rev = _num(revenue_row[col]) if revenue_row is not None and col in revenue_row.index else None
        ni = _num(ni_row[col]) if ni_row is not None and col in ni_row.index else None
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
    """
    import yfinance as yf

    t_up = ticker.upper().strip()
    try:
        t = yf.Ticker(t_up)
        info: Dict[str, Any] = t.info or {}

        # ---- Company info ----
        company_info = _build_company_info(info)
        from .spot import resolve_spot

        spot_q = resolve_spot(t_up)
        if spot_q is not None:
            company_info["current_price"] = round(spot_q.price, 4)
            company_info["spot_source"] = spot_q.source
            prev = company_info.get("previous_close")
            if prev is not None and prev != 0:
                company_info["price_change"] = round(spot_q.price - prev, 4)
                company_info["price_change_pct"] = round(
                    (spot_q.price - prev) / prev * 100, 4
                )

        # ---- Price history for every supported period ----
        price_history: Dict[str, List[Dict[str, Any]]] = {}
        for period in _PERIOD_INTERVAL_MAP:
            bars = _fetch_price_history(t, period)
            price_history[period] = bars

        # ---- Consolidated metrics ----
        metrics = _build_metrics(info)

        # ---- Financial performance ----
        quarterly_financials: List[Dict[str, Any]] = []
        annual_financials: List[Dict[str, Any]] = []
        try:
            quarterly_financials = _extract_financials(t.quarterly_income_stmt)
        except Exception as exc:
            logger.warning("Quarterly income stmt failed for %s: %s", t_up, exc)
        try:
            annual_financials = _extract_financials(t.income_stmt)
        except Exception as exc:
            logger.warning("Annual income stmt failed for %s: %s", t_up, exc)

        return {
            "ticker": t_up,
            "company_info": company_info,
            "price_history": price_history,
            "metrics": metrics,
            "financials": {
                "quarterly": quarterly_financials,
                "annual": annual_financials,
            },
        }

    except Exception as exc:
        logger.error(
            "[StockFundamentalsConnector] fetch failed for %s: %s", t_up, exc,
        )
        raise
