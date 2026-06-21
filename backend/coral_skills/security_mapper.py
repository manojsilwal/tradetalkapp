"""
CORAL Hub Skill: Security Mapping & Sector Agent

Responsible for:
- Mapping CUSIPs or Issuer Names to standard Tickers using yfinance / internal master.
- Fetching sector and industry classifications from yfinance.
- Flagging unmapped or ambiguous securities.
"""
import asyncio
import logging
import yfinance as yf
from typing import Dict, Any, List

from backend.coral_agents import hub_add_note

logger = logging.getLogger(__name__)

async def map_security_and_sector(cusip: str, issuer_name: str, symbol_hint: str = "") -> Dict[str, Any]:
    """
    Given a CUSIP or issuer name (often from a 13F), attempt to map it to a ticker
    and fetch its sector/industry.

    Since we don't have OpenFIGI configured by default, we'll use symbol_hint
    or fuzzy matching on issuer_name against a local cache or rely on yfinance's search.
    For this MVP, we simulate mapping by passing in a symbol_hint or falling back to unmapped.
    """
    logger.info(f"[Security Mapper] Mapping security: {issuer_name} (CUSIP: {cusip})")

    ticker_symbol = symbol_hint
    mapping_status = "unmapped"
    sector = "Unknown"
    industry = "Unknown"

    if ticker_symbol:
        try:
            # Note: yfinance is blocking/synchronous, so we wrap it
            ticker = yf.Ticker(ticker_symbol)
            info = await asyncio.to_thread(lambda: ticker.info)

            if info and 'sector' in info:
                sector = info.get('sector', 'Unknown')
                industry = info.get('industry', 'Unknown')
                mapping_status = "mapped"
        except Exception as e:
            logger.warning(f"[Security Mapper] Failed to fetch info for {ticker_symbol}: {e}")
            mapping_status = "ambiguous"
    else:
        # Fallback simulation mapping for MVP
        normalized_name = issuer_name.lower()
        if "apple" in normalized_name:
            ticker_symbol = "AAPL"
            sector = "Technology"
            mapping_status = "mapped"
        elif "microsoft" in normalized_name:
            ticker_symbol = "MSFT"
            sector = "Technology"
            mapping_status = "mapped"

    if mapping_status == "mapped":
        hub_add_note(
            "data_ingest",
            f"Mapped {issuer_name} (CUSIP: {cusip}) to {ticker_symbol} in sector {sector}"
        )
    else:
        hub_add_note(
            "data_ingest",
            f"Failed to map {issuer_name} (CUSIP: {cusip}) to a known ticker."
        )

    return {
        "cusip": cusip,
        "issuer_name": issuer_name,
        "ticker": ticker_symbol,
        "sector": sector,
        "industry": industry,
        "mapping_status": mapping_status
    }

async def batch_map_securities(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Takes a list of holdings (from Phase 3) and applies mapping to each.
    """
    mapped_holdings = []

    # In production, we'd batch OpenFIGI requests. Here we concurrently process them.
    tasks = []
    for h in holdings:
        tasks.append(map_security_and_sector(h.get("cusip", ""), h.get("issuer_name", "")))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, h in enumerate(holdings):
        mapped_holding = h.copy()
        res = results[i]
        if isinstance(res, dict):
            mapped_holding["ticker"] = res["ticker"]
            mapped_holding["sector"] = res["sector"]
            mapped_holding["industry"] = res["industry"]
            mapped_holding["mapping_status"] = res["mapping_status"]
        else:
            mapped_holding["ticker"] = ""
            mapped_holding["sector"] = "Unknown"
            mapped_holding["mapping_status"] = "error"

        mapped_holdings.append(mapped_holding)

    return mapped_holdings
