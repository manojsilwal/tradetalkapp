"""
market_context.py
-----------------
Maps a stock ticker to its sector, index membership, and relevant
index/ETF symbols for broadening prediction-market searches.

Used by polymarket.py and kalshi.py to include sector/index-level bets
alongside company-specific bets.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Static sector / index map for common tickers ────────────────────────────
# Format: ticker → (sector_label, [index_keywords], [etf_synonyms])
_STATIC_MAP: Dict[str, Tuple[str, List[str], List[str]]] = {
    # ─ Big Tech / Consumer Electronics ─
    "AAPL":  ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK"]),
    "MSFT":  ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK"]),
    "GOOG":  ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK"]),
    "GOOGL": ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK"]),
    "META":  ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK"]),
    "NVDA":  ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK", "SOXX"]),
    "AMD":   ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK", "SOXX"]),
    "INTC":  ("Technology", ["Nasdaq", "S&P 500", "QQQ"],        ["SPY", "SPX", "QQQ", "XLK", "SOXX"]),
    "AVGO":  ("Technology", ["Nasdaq", "S&P 500"],               ["SPY", "SPX", "XLK", "SOXX"]),
    "MRVL":  ("Technology", ["Nasdaq", "S&P 500"],               ["SPY", "SPX", "XLK", "SOXX"]),
    "TSM":   ("Technology", ["Nasdaq"],                          ["SPY", "XLK", "SOXX"]),
    "ASML":  ("Technology", ["Nasdaq"],                          ["SPY", "XLK", "SOXX"]),
    # ─ E-commerce / Consumer ─
    "AMZN":  ("Consumer Discretionary", ["Nasdaq", "S&P 500"],  ["SPY", "SPX", "QQQ", "XLY"]),
    "TSLA":  ("Consumer Discretionary", ["Nasdaq", "S&P 500"],  ["SPY", "SPX", "QQQ", "XLY"]),
    "NFLX":  ("Communication Services", ["Nasdaq", "S&P 500"],  ["SPY", "SPX", "QQQ", "XLC"]),
    "DIS":   ("Communication Services", ["S&P 500"],             ["SPY", "SPX", "XLC"]),
    # ─ Financials ─
    "JPM":   ("Financials", ["Dow Jones", "S&P 500"],            ["SPY", "SPX", "XLF"]),
    "BAC":   ("Financials", ["Dow Jones", "S&P 500"],            ["SPY", "SPX", "XLF"]),
    "GS":    ("Financials", ["Dow Jones", "S&P 500"],            ["SPY", "SPX", "XLF"]),
    # ─ Healthcare ─
    "JNJ":   ("Healthcare", ["Dow Jones", "S&P 500"],            ["SPY", "SPX", "XLV"]),
    "LLY":   ("Healthcare", ["S&P 500"],                         ["SPY", "SPX", "XLV"]),
    "PFE":   ("Healthcare", ["Dow Jones", "S&P 500"],            ["SPY", "SPX", "XLV"]),
    # ─ Energy ─
    "XOM":   ("Energy", ["Dow Jones", "S&P 500"],                ["SPY", "SPX", "XLE"]),
    "CVX":   ("Energy", ["Dow Jones", "S&P 500"],                ["SPY", "SPX", "XLE"]),
    # ─ Crypto-adjacent ─
    "MSTR":  ("Technology", ["Nasdaq"],                          ["SPY", "QQQ"]),
    "COIN":  ("Financials", ["Nasdaq"],                          ["SPY", "QQQ"]),
    # ─ Retail ─
    "WMT":   ("Consumer Staples", ["Dow Jones", "S&P 500"],      ["SPY", "SPX", "XLP"]),
    "COST":  ("Consumer Staples", ["Nasdaq", "S&P 500"],         ["SPY", "SPX", "XLP"]),
    # ─ GME / meme ─
    "GME":   ("Consumer Discretionary", ["S&P 500"],             ["SPY", "SPX", "XLY"]),
}

# Index/ETF → search keywords for prediction-market text matching
_INDEX_KEYWORDS: Dict[str, List[str]] = {
    "S&P 500": ["S&P 500", "SPX", "SPY", "S&P500"],
    "Nasdaq":  ["Nasdaq", "QQQ", "NDX", "NASDAQ-100", "Nasdaq 100"],
    "Dow Jones": ["Dow Jones", "DJIA", "DIA"],
    "QQQ":     ["QQQ", "Nasdaq", "NDX"],
    "XLK":     ["XLK", "technology sector", "tech sector"],
    "XLF":     ["XLF", "financial sector", "financials"],
    "XLV":     ["XLV", "healthcare sector", "health care"],
    "XLY":     ["XLY", "consumer discretionary"],
    "XLE":     ["XLE", "energy sector"],
    "XLC":     ["XLC", "communication services"],
    "XLP":     ["XLP", "consumer staples"],
    "SOXX":    ["SOXX", "semiconductor", "semiconductors"],
}


def get_ticker_context(ticker: str) -> Dict:
    """
    Return sector, index keywords, and ETF synonyms for a ticker.
    Falls back to yfinance for unknown tickers.
    """
    upper = ticker.upper()
    entry = _STATIC_MAP.get(upper)
    if entry:
        sector, indices, etfs = entry
    else:
        sector = None
        indices = ["S&P 500", "Nasdaq"]
        etfs = ["SPY", "SPX", "QQQ"]

    # Build flat list of all index/sector search keywords
    index_search_terms: List[str] = []
    for idx in indices:
        index_search_terms.extend(_INDEX_KEYWORDS.get(idx, [idx]))
    for etf in etfs:
        index_search_terms.extend(_INDEX_KEYWORDS.get(etf, [etf]))

    # Deduplicate while preserving order
    seen: set = set()
    deduped: List[str] = []
    for t in index_search_terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            deduped.append(t)

    return {
        "sector": sector,
        "indices": indices,
        "etfs": etfs,
        "index_search_terms": deduped,
    }


async def get_ticker_context_with_yfinance(ticker: str) -> Dict:
    """
    Same as get_ticker_context but enriches with live yfinance sector/industry
    for tickers not in the static map.
    """
    upper = ticker.upper()
    ctx = get_ticker_context(upper)
    if ctx["sector"] is not None:
        return ctx

    def _yf_lookup():
        try:
            import yfinance as yf
            info = yf.Ticker(upper).info or {}
            return {
                "sector": info.get("sector"),
                "industry": info.get("industry"),
            }
        except Exception:
            return {}

    yf_data = await asyncio.to_thread(_yf_lookup)
    sector = yf_data.get("sector")
    if sector:
        ctx["sector"] = sector
        # Map common sector names to relevant index/ETF terms
        _sector_to_etf = {
            "Technology": ["Nasdaq", "XLK"],
            "Consumer Discretionary": ["Nasdaq", "XLY"],
            "Financials": ["XLF"],
            "Healthcare": ["XLV"],
            "Energy": ["XLE"],
            "Communication Services": ["XLC"],
            "Consumer Staples": ["XLP"],
            "Industrials": ["XLI"],
            "Real Estate": ["XLRE"],
            "Utilities": ["XLU"],
            "Materials": ["XLB"],
        }
        etfs_for_sector = _sector_to_etf.get(sector, [])
        extra_terms: List[str] = []
        for etf in etfs_for_sector:
            extra_terms.extend(_INDEX_KEYWORDS.get(etf, [etf]))
        existing = {t.lower() for t in ctx["index_search_terms"]}
        for t in extra_terms:
            if t.lower() not in existing:
                ctx["index_search_terms"].append(t)
                existing.add(t.lower())

    return ctx
