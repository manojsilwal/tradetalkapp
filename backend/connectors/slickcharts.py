"""Slickcharts.com S&P 500 movers + index ETF benchmarks via FinCrawler HTML fetch."""

from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SLICKCHARTS_GAINERS_URL = "https://www.slickcharts.com/sp500/gainers"
SLICKCHARTS_LOSERS_URL = "https://www.slickcharts.com/sp500/losers"

_MOVER_ROW_RE = re.compile(
    r'<tr><td[^>]*>\s*<a href="/symbol/([A-Z.]+)">([^<]*)</a>\s*</td>'
    r'<td>\s*<a href="/symbol/\1">\1</a>\s*</td>'
    r'<td[^>]*>.*?<span>([\d.,]+)</span>.*?</td>'
    r'<td[^>]*>([-\d.,]+)</td>'
    r'<td[^>]*>([-\d.,]+%)</td>\s*</tr>',
    re.S,
)

_ETF_ROW_RE = re.compile(
    r'<tr><td class="text-nowrap"><a href="/symbol/([A-Z]+)">\1</a></td>'
    r'<td class="text-nowrap">([^<]*)</td>'
    r'<td[^>]*>.*?<span>([\d.,]+)</span>.*?</td>'
    r'<td[^>]*>([-\d.,]+)</td>'
    r'<td[^>]*>([-\d.,]+%)</td></tr>',
    re.S,
)


def _parse_pct(raw: str) -> Optional[float]:
    if not raw:
        return None
    cleaned = str(raw).strip().rstrip("%").replace(",", "")
    try:
        return round(float(cleaned), 4)
    except (TypeError, ValueError):
        return None


def _parse_num(raw: str) -> Optional[float]:
    if not raw:
        return None
    cleaned = str(raw).strip().replace(",", "")
    try:
        return round(float(cleaned), 4)
    except (TypeError, ValueError):
        return None


def parse_mover_rows(html: str, *, bucket: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Parse gainers/losers table rows from a Slickcharts movers page."""
    rows: List[Dict[str, Any]] = []
    for i, match in enumerate(_MOVER_ROW_RE.finditer(html or ""), start=1):
        sym, company, price_raw, chg_raw, pct_raw = match.groups()
        pct = _parse_pct(pct_raw)
        if pct is None:
            continue
        rows.append(
            {
                "symbol": sym.upper(),
                "company_name": html_lib.unescape(company.strip()),
                "close": _parse_num(price_raw),
                "change": _parse_num(chg_raw),
                "daily_return_pct": pct,
                "bucket": bucket,
                "rank": i,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def parse_etf_benchmarks(html: str) -> List[Dict[str, Any]]:
    """Parse SPY/QQQ/DIA footer ETF table from a Slickcharts page."""
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for match in _ETF_ROW_RE.finditer(html or ""):
        sym, name, price_raw, chg_raw, pct_raw = match.groups()
        sym = sym.upper()
        if sym in seen:
            continue
        seen.add(sym)
        pct = _parse_pct(pct_raw)
        out.append(
            {
                "symbol": sym,
                "name": html_lib.unescape(name.strip()),
                "price": _parse_num(price_raw),
                "change": _parse_num(chg_raw),
                "daily_return_pct": pct,
            }
        )
    return out


def _fetch_html(url: str, *, force_refresh: bool = False) -> str:
    from backend.fincrawler_client import fc

    if not fc.enabled:
        return ""
    return fc.fetch_html_sync(url, force_refresh=force_refresh) or ""


def fetch_slickcharts_movers(
    n_losers: int = 20,
    n_gainers: int = 10,
    *,
    force_refresh: bool = False,
) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """
    Fetch live S&P 500 gainers/losers and index ETF benchmarks from Slickcharts.

    Returns (gainers, losers, etfs) or None when FinCrawler is disabled/unreachable.
    """
    try:
        gainers_html = _fetch_html(SLICKCHARTS_GAINERS_URL, force_refresh=force_refresh)
        losers_html = _fetch_html(SLICKCHARTS_LOSERS_URL, force_refresh=force_refresh)
    except Exception as e:
        logger.warning("[Slickcharts] HTML fetch failed: %s", e)
        return None

    if not gainers_html and not losers_html:
        return None

    gainers = parse_mover_rows(gainers_html, bucket="gainer", limit=n_gainers)
    losers = parse_mover_rows(losers_html, bucket="loser", limit=n_losers)
    etfs = parse_etf_benchmarks(gainers_html) or parse_etf_benchmarks(losers_html)

    if not gainers and not losers:
        logger.warning("[Slickcharts] parse returned no mover rows")
        return None

    logger.info(
        "[Slickcharts] fetched %d gainers, %d losers, %d etfs",
        len(gainers),
        len(losers),
        len(etfs),
    )
    return gainers, losers, etfs


def slickcharts_rows_to_brief_rows(
    gainers: List[Dict[str, Any]],
    losers: List[Dict[str, Any]],
) -> List[tuple[str, int, Dict[str, Any]]]:
    """Map parsed Slickcharts rows into (bucket, rank, raw) for _normalize_row."""
    today = date.today().isoformat()
    out: List[tuple[str, int, Dict[str, Any]]] = []
    for bucket, items in (("loser", losers), ("gainer", gainers)):
        for rank, item in enumerate(items, start=1):
            out.append(
                (
                    bucket,
                    rank,
                    {
                        "symbol": item["symbol"],
                        "trade_date": today,
                        "close": item.get("close"),
                        "daily_return_pct": item.get("daily_return_pct"),
                        "volume": 0,
                        "relative_volume": 1.0,
                        "return_zscore_60d": 0.0,
                        "catalyst_status": "no_catalyst",
                        "primary_cause_category": "none",
                        "primary_cause_headline": "",
                        "primary_cause_weight": 0.0,
                        "market_regime": "Balanced",
                        "company_name": item.get("company_name"),
                    },
                )
            )
    return out
