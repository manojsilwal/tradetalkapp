"""
Bundled S&P 500 GICS sector / sub-industry reference (Wikipedia snapshot).

Used when yfinance ``.info`` is rate-limited so Daily Brief / Morning Brief tables
still show industry labels instead of N/A. Market cap and P/E still require live
fundamentals when Yahoo is blocked.
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent / "data" / "sp500_gics_reference.json"

# Tickers absent from the bundled Wikipedia snapshot (portfolio / macro picks).
_MANUAL_OVERRIDES: Dict[str, Dict[str, str]] = {
    "MRVL": {
        "company_name": "Marvell Technology",
        "sector": "Technology",
        "industry": "Semiconductors",
    },
}


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").upper().strip().replace(".", "-")


@lru_cache(maxsize=1)
def _load_bundled() -> Dict[str, Dict[str, str]]:
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[SP500GICS] bundled reference missing (%s): %s", _DATA_PATH, e)
        raw = {}
    out = {str(k).upper(): dict(v) for k, v in raw.items()}
    for sym, meta in _MANUAL_OVERRIDES.items():
        out[sym.upper()] = dict(meta)
    return out


def _refresh_from_wikipedia() -> Dict[str, Dict[str, str]]:
    """Best-effort live refresh (dev / cron); failures keep bundled file."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradeTalk/1.0 (sp500 gics reference)"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        html_doc = resp.read().decode("utf-8", errors="replace")
    m = re.search(r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>', html_doc, re.S)
    if not m:
        raise RuntimeError("Wikipedia S&P 500 table not found")
    rows = re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S)
    header = [
        html.unescape(re.sub(r"<[^>]+>", "", c).strip())
        for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], re.S)
    ]
    idx = {name: i for i, name in enumerate(header)}
    out: Dict[str, Dict[str, str]] = {}
    for row in rows[1:]:
        cols = [
            html.unescape(re.sub(r"<[^>]+>", "", c).strip())
            for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
        ]
        if not cols:
            continue
        sym = _normalize_symbol(cols[idx["Symbol"]])
        out[sym] = {
            "company_name": cols[idx["Security"]],
            "sector": cols[idx["GICS Sector"]],
            "industry": cols[idx["GICS Sub-Industry"]],
        }
    for sym, meta in _MANUAL_OVERRIDES.items():
        out[sym.upper()] = dict(meta)
    return out


def get_sp500_gics(symbol: str) -> Optional[Dict[str, str]]:
    """Return {company_name, sector, industry} for an S&P 500 ticker, or None."""
    sym = _normalize_symbol(symbol)
    if not sym:
        return None
    table = _load_bundled()
    row = table.get(sym)
    if row:
        return dict(row)
    return _MANUAL_OVERRIDES.get(sym)


def gics_to_enrichment(row: Dict[str, str]) -> Dict[str, Any]:
    return {
        "company_name": row.get("company_name"),
        "sector": row.get("sector") or "Unknown",
        "industry": row.get("industry") or "Unknown",
        "market_cap": None,
        "pe_ratio": None,
        "forward_pe": None,
        "insider_sentiment": "N/A",
        "source": "sp500_gics_reference",
    }
