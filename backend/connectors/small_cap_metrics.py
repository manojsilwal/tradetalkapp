"""Growth-stage metrics for small / micro cap assessment."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import yfinance as yf

from ..paper_portfolio import _classify_market_cap
from .base import DataConnector

_REVENUE_ROW_NAMES = ("Total Revenue", "Revenue", "Operating Revenue")
_GROSS_PROFIT_ROWS = ("Gross Profit",)
_OPERATING_INCOME_ROWS = ("Operating Income", "EBIT")


def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def _find_row(df: Any, names: tuple[str, ...]) -> Any:
    if df is None or getattr(df, "empty", True):
        return None
    for name in names:
        if name in df.index:
            return df.loc[name]
    return None


def _series_from_row(row: Any, limit: int = 8) -> List[Dict[str, Any]]:
    if row is None:
        return []
    out: List[Dict[str, Any]] = []
    for col in row.index:
        val = _num(row[col])
        if val is None:
            continue
        period = col.date().isoformat() if hasattr(col, "date") else str(col)
        out.append({"period": period, "value": val})
    out.sort(key=lambda x: x["period"], reverse=True)
    return out[:limit]


def _yoy_growth(series: List[Dict[str, Any]], max_points: int = 3) -> List[Dict[str, Any]]:
    if len(series) < 2:
        return []
    out: List[Dict[str, Any]] = []
    for i in range(min(max_points, len(series) - 1)):
        curr = series[i]["value"]
        prev = series[i + 1]["value"]
        if not prev:
            continue
        growth = ((curr - prev) / abs(prev)) * 100.0
        out.append(
            {
                "period": series[i]["period"],
                "value": curr,
                "yoy_growth_pct": round(growth, 2),
            }
        )
    return out


def _margin_series(
    financials: Any,
    *,
    revenue_row: Any,
    profit_row: Any,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    if revenue_row is None or profit_row is None:
        return []
    margins: List[Dict[str, Any]] = []
    for col in revenue_row.index:
        rev = _num(revenue_row[col])
        profit = _num(profit_row[col]) if col in profit_row.index else None
        if rev is None or not rev or profit is None:
            continue
        period = col.date().isoformat() if hasattr(col, "date") else str(col)
        margins.append(
            {
                "period": period,
                "margin_pct": round((profit / rev) * 100.0, 2),
            }
        )
    margins.sort(key=lambda x: x["period"], reverse=True)
    return margins[:limit]


_SKIP_REVENUE_ROW_NAMES = frozenset({
    "Total Revenue",
    "Revenue",
    "Operating Revenue",
    "Cost Of Revenue",
    "Other Revenue",
    "Excise Taxes",
    "Reconciled Cost Of Revenue",
})


def _year_label(col: Any) -> str:
    if hasattr(col, "year"):
        return str(col.year)
    text = str(col)
    return text[:4] if len(text) >= 4 else text


def _annual_columns(financials: Any, limit: int = 5) -> List[Any]:
    if financials is None or getattr(financials, "empty", True):
        return []
    cols = list(financials.columns)
    cols.sort(reverse=True)
    return cols[:limit]


def _company_revenue_history_5y(
    financials: Any,
    revenue_row: Any,
    gross_row: Any,
    op_row: Any,
) -> List[Dict[str, Any]]:
    if revenue_row is None:
        return []
    out: List[Dict[str, Any]] = []
    for col in _annual_columns(financials, 5):
        if col not in revenue_row.index:
            continue
        rev = _num(revenue_row[col])
        if rev is None:
            continue
        gross = _num(gross_row[col]) if gross_row is not None and col in gross_row.index else None
        op = _num(op_row[col]) if op_row is not None and col in op_row.index else None
        gross_margin = round((gross / rev) * 100.0, 2) if gross is not None and rev else None
        op_margin = round((op / rev) * 100.0, 2) if op is not None and rev else None
        out.append(
            {
                "year": _year_label(col),
                "revenue_usd": rev,
                "gross_margin_pct": gross_margin,
                "operating_margin_pct": op_margin,
            }
        )
    out.sort(key=lambda x: x["year"])
    return out


def _segment_revenue_streams(financials: Any, limit_years: int = 5) -> List[Dict[str, Any]]:
    if financials is None or getattr(financials, "empty", True):
        return []
    streams: List[Dict[str, Any]] = []
    for idx in financials.index:
        name = str(idx).strip()
        lower = name.lower()
        if "revenue" not in lower or name in _SKIP_REVENUE_ROW_NAMES:
            continue
        if lower.startswith("cost") or "reconciled" in lower:
            continue
        row = financials.loc[idx]
        years: List[Dict[str, Any]] = []
        for col in _annual_columns(financials, limit_years):
            if col not in row.index:
                continue
            val = _num(row[col])
            if val is None:
                continue
            years.append({"year": _year_label(col), "revenue_usd": val, "gross_margin_pct": None, "operating_margin_pct": None})
        if len(years) >= 2:
            clean_name = name.replace(" Revenue", "").replace(" Revenues", "").strip()
            streams.append({"name": clean_name or name, "years": sorted(years, key=lambda x: x["year"])})
    return streams[:8]


def _fetch_news_headlines(ticker: Any, limit: int = 15) -> List[Dict[str, str]]:
    try:
        raw = getattr(ticker, "news", None) or []
    except Exception:
        return []
    out: List[Dict[str, str]] = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title,
                "publisher": str(item.get("publisher") or item.get("publisherName") or "").strip(),
                "link": str(item.get("link") or "").strip(),
                "source": "yfinance",
            }
        )
    return out


def _holders_table(df: Any, limit: int = 5) -> List[Dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    rows: List[Dict[str, Any]] = []
    pct_col = "% Out" if "% Out" in df.columns else ("pctHeld" if "pctHeld" in df.columns else None)
    name_col = "Holder" if "Holder" in df.columns else None
    for _, row in df.head(limit).iterrows():
        name = str(row.get(name_col, row.iloc[0] if len(row) else "Unknown"))
        pct = _num(row.get(pct_col)) if pct_col else None
        if pct is not None and pct <= 1.0:
            pct = round(pct * 100.0, 2)
        rows.append({"name": name, "pct_held": pct})
    return rows


def _fetch_small_cap_bundle(ticker_sym: str) -> Dict[str, Any]:
    ticker = yf.Ticker(ticker_sym)
    info = ticker.info or {}

    market_cap = _num(info.get("marketCap"))
    cap_bucket = _classify_market_cap(market_cap)

    financials = ticker.financials
    quarterly = ticker.quarterly_financials

    revenue_row = _find_row(financials, _REVENUE_ROW_NAMES)
    q_revenue_row = _find_row(quarterly, _REVENUE_ROW_NAMES) or revenue_row
    gross_row = _find_row(financials, _GROSS_PROFIT_ROWS)
    q_gross_row = _find_row(quarterly, _GROSS_PROFIT_ROWS) or gross_row
    op_row = _find_row(financials, _OPERATING_INCOME_ROWS)
    q_op_row = _find_row(quarterly, _OPERATING_INCOME_ROWS) or op_row

    revenue_series = _series_from_row(revenue_row)
    revenue_yoy = _yoy_growth(revenue_series, max_points=3)

    gross_margins = _margin_series(financials, revenue_row=revenue_row, profit_row=gross_row)
    q_gross_margins = _margin_series(quarterly, revenue_row=q_revenue_row, profit_row=q_gross_row)
    operating_margins = _margin_series(financials, revenue_row=revenue_row, profit_row=op_row)
    q_operating_margins = _margin_series(quarterly, revenue_row=q_revenue_row, profit_row=q_op_row)

    company_revenue_history_5y = _company_revenue_history_5y(
        financials, revenue_row, gross_row, op_row
    )
    segment_revenue_streams = _segment_revenue_streams(financials, limit_years=5)
    news_headlines = _fetch_news_headlines(ticker, limit=15)

    officers_raw = info.get("companyOfficers") or []
    officers: List[Dict[str, Any]] = []
    for off in officers_raw[:8]:
        if not isinstance(off, dict):
            continue
        officers.append(
            {
                "name": off.get("name") or "",
                "title": off.get("title") or off.get("position") or "",
                "age": off.get("age"),
                "year_born": off.get("yearBorn"),
            }
        )

    inst_holders = _holders_table(getattr(ticker, "institutional_holders", None), limit=5)
    fund_holders = _holders_table(getattr(ticker, "mutualfund_holders", None), limit=3)

    total_cash = _num(info.get("totalCash"))
    total_debt = _num(info.get("totalDebt"))
    operating_cashflow = _num(info.get("operatingCashflow"))
    net_income = _num(info.get("netIncomeToCommon") or info.get("netIncome"))
    revenue_growth = _num(info.get("revenueGrowth"))
    earnings_growth = _num(info.get("earningsGrowth"))
    forward_eps = _num(info.get("forwardEps"))
    trailing_eps = _num(info.get("trailingEps"))
    profit_margins = _num(info.get("profitMargins"))
    gross_margin_info = _num(info.get("grossMargins"))
    inst_ownership_pct = _num(info.get("heldPercentInstitutions"))
    if inst_ownership_pct is not None and inst_ownership_pct <= 1.0:
        inst_ownership_pct = round(inst_ownership_pct * 100.0, 2)

    return {
        "ticker": ticker_sym,
        "market_cap": market_cap,
        "cap_bucket": cap_bucket,
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "long_business_summary": (info.get("longBusinessSummary") or "")[:4000],
        "revenue_yoy": revenue_yoy,
        "revenue_series": revenue_series[:4],
        "gross_margins_annual": gross_margins[:4],
        "gross_margins_quarterly": q_gross_margins[:6],
        "operating_margins_annual": operating_margins[:4],
        "operating_margins_quarterly": q_operating_margins[:6],
        "company_revenue_history_5y": company_revenue_history_5y,
        "segment_revenue_streams": segment_revenue_streams,
        "news_headlines": news_headlines,
        "institutional_holders": inst_holders,
        "mutualfund_holders": fund_holders,
        "institutional_ownership_pct": inst_ownership_pct,
        "officers": officers,
        "total_cash": total_cash,
        "total_debt": total_debt,
        "operating_cashflow": operating_cashflow,
        "net_income": net_income,
        "revenue_growth_yoy_pct": round(revenue_growth * 100, 2) if revenue_growth is not None else None,
        "earnings_growth_yoy_pct": round(earnings_growth * 100, 2) if earnings_growth is not None else None,
        "forward_eps": forward_eps,
        "trailing_eps": trailing_eps,
        "profit_margins_pct": round(profit_margins * 100, 2) if profit_margins is not None else None,
        "gross_margins_pct": round(gross_margin_info * 100, 2) if gross_margin_info is not None else None,
        "full_time_employees": info.get("fullTimeEmployees"),
    }


async def _fetch_fincrawler_enrichment(ticker_sym: str) -> Dict[str, Any]:
    """SEC filing excerpts + structured news via FinCrawler (optional)."""
    try:
        from ..fincrawler_client import fc
    except Exception:
        return {"fincrawler_enabled": False}

    if not fc.enabled:
        return {"fincrawler_enabled": False}

    sec_10k, sec_10q, sec_8k, news_articles = await asyncio.gather(
        fc.get_sec_filing(ticker_sym, "10-K", max_chars=14000),
        fc.get_sec_filing(ticker_sym, "10-Q", max_chars=8000),
        fc.get_sec_filing(ticker_sym, "8-K", max_chars=8000),
        fc.get_stock_news_articles(ticker_sym, limit=12),
        return_exceptions=True,
    )

    def _as_text(val: Any) -> str:
        if isinstance(val, str):
            return val.strip()
        return ""

    def _as_articles(val: Any) -> List[Dict[str, str]]:
        if isinstance(val, list):
            return [a for a in val if isinstance(a, dict)]
        return []

    news_summaries = [
        f"{a.get('title', '')}: {a.get('summary', '')}".strip(": ")
        for a in _as_articles(news_articles)
        if a.get("title")
    ]

    return {
        "fincrawler_enabled": True,
        "fincrawler_sec_10k_excerpt": _as_text(sec_10k),
        "fincrawler_sec_10q_excerpt": _as_text(sec_10q),
        "fincrawler_sec_8k_excerpt": _as_text(sec_8k),
        "fincrawler_news_articles": _as_articles(news_articles),
        "fincrawler_news_summaries": news_summaries,
    }


def _merge_news_headlines(
    yf_headlines: List[Dict[str, str]],
    fc_articles: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Combine yfinance titles with FinCrawler articles; dedupe by normalized title."""
    merged: List[Dict[str, str]] = []
    seen: set[str] = set()

    def _add(item: Dict[str, str]) -> None:
        title = str(item.get("title") or "").strip()
        if not title:
            return
        key = title.lower()[:120]
        if key in seen:
            return
        seen.add(key)
        merged.append(item)

    for h in yf_headlines or []:
        if isinstance(h, dict):
            _add({**h, "source": h.get("source") or "yfinance"})
    for a in fc_articles or []:
        if isinstance(a, dict):
            _add(a)
    return merged[:20]


class SmallCapMetricsConnector(DataConnector):
    """Fetch quantitative inputs for growth-stage small cap assessment."""

    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        ticker_sym = (kwargs.get("ticker") or "").strip().upper()
        if not ticker_sym:
            return {"error": "missing_ticker", "ticker": ""}

        try:
            yf_task = asyncio.to_thread(_fetch_small_cap_bundle, ticker_sym)
            fc_task = _fetch_fincrawler_enrichment(ticker_sym)
            data, fc_data = await asyncio.gather(yf_task, fc_task)

            if isinstance(fc_data, dict) and fc_data.get("fincrawler_enabled"):
                data["fincrawler_sec_10k_excerpt"] = fc_data.get("fincrawler_sec_10k_excerpt") or ""
                data["fincrawler_sec_10q_excerpt"] = fc_data.get("fincrawler_sec_10q_excerpt") or ""
                data["fincrawler_sec_8k_excerpt"] = fc_data.get("fincrawler_sec_8k_excerpt") or ""
                data["fincrawler_news_summaries"] = fc_data.get("fincrawler_news_summaries") or []
                data["fincrawler_news_articles"] = fc_data.get("fincrawler_news_articles") or []
                data["news_headlines"] = _merge_news_headlines(
                    data.get("news_headlines") or [],
                    data.get("fincrawler_news_articles") or [],
                )
            else:
                data["fincrawler_enabled"] = False

            if not data.get("market_cap"):
                return {"error": "market_cap_unavailable", "ticker": ticker_sym}
            return data
        except Exception as exc:
            return {"error": str(exc), "ticker": ticker_sym}
