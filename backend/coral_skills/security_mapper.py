"""
CORAL Hub Skill: Security Mapping & Sector Agent

Responsible for:
- Mapping CUSIPs or Issuer Names to standard Tickers using yfinance / internal master.
- Fetching sector and industry classifications from yfinance.
- Flagging unmapped or ambiguous securities.
"""
import asyncio
import logging
import os
import yfinance as yf
import httpx
from typing import Dict, Any, List, Optional

from backend.coral_agents import hub_add_note

logger = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

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


# ── OpenFIGI CUSIP -> ticker resolution (cached) ────────────────────────────────

def _openfigi_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("OPENFIGI_API_KEY", "").strip()
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    return headers


def _openfigi_limits() -> Dict[str, float]:
    """Batch size + inter-request delay depend on whether an API key is set."""
    has_key = bool(os.environ.get("OPENFIGI_API_KEY", "").strip())
    if has_key:
        return {"batch": 100, "delay": 0.3}
    # Keyless: 25 requests/min, 10 jobs/request -> ~3s between requests (use 7s safe).
    return {"batch": 10, "delay": 7.0}


def _pick_ticker(data_items: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """From an OpenFIGI mapping 'data' list, pick a US common-stock ticker."""
    if not data_items:
        return None
    preferred = [d for d in data_items if (d.get("exchCode") in ("US", "UN", "UQ", "UW", "UR"))]
    pool = preferred or data_items
    for d in pool:
        ticker = (d.get("ticker") or "").strip()
        if ticker and "/" not in ticker:
            return {"ticker": ticker, "name": d.get("name") or ""}
    return None


async def _openfigi_resolve(cusips: List[str]) -> Dict[str, Optional[Dict[str, str]]]:
    """Resolve a list of CUSIPs to tickers via OpenFIGI in rate-limited batches."""
    out: Dict[str, Optional[Dict[str, str]]] = {}
    if not cusips:
        return out
    limits = _openfigi_limits()
    batch_size = int(limits["batch"])
    delay = limits["delay"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for start in range(0, len(cusips), batch_size):
            batch = cusips[start:start + batch_size]
            payload = [{"idType": "ID_CUSIP", "value": c, "exchCode": "US"} for c in batch]
            try:
                resp = await client.post(OPENFIGI_URL, headers=_openfigi_headers(), json=payload)
                if resp.status_code == 429:
                    logger.warning("[Security Mapper] OpenFIGI rate-limited; backing off")
                    await asyncio.sleep(max(delay, 10.0))
                    continue
                resp.raise_for_status()
                results = resp.json()
            except Exception as e:
                logger.warning("[Security Mapper] OpenFIGI batch failed: %s", e)
                for c in batch:
                    out.setdefault(c, None)
                await asyncio.sleep(delay)
                continue

            for cusip, item in zip(batch, results):
                if isinstance(item, dict) and item.get("data"):
                    out[cusip] = _pick_ticker(item["data"])
                else:
                    out[cusip] = None

            if start + batch_size < len(cusips):
                await asyncio.sleep(delay)
    return out


async def map_holdings_to_tickers(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Resolve a 13F holdings list (issuer_name + cusip) to tickers, using a
    persistent CUSIP cache and OpenFIGI for cache misses. Adds ``ticker``,
    ``sector``, and ``mapping_status`` keys to each holding.
    """
    from backend import fund_leaderboard_store as store

    enriched: List[Dict[str, Any]] = []
    cusips = []
    for h in holdings:
        c = (h.get("cusip") or "").strip()
        if c:
            cusips.append(c)
    unique_cusips = sorted(set(cusips))

    # 1. Cache lookups
    cache_hits: Dict[str, Dict[str, Any]] = {}
    misses: List[str] = []
    for c in unique_cusips:
        cached = store.cache_get_ticker(c)
        if cached:
            cache_hits[c] = cached
        else:
            misses.append(c)

    # 2. Resolve misses via OpenFIGI, persist to cache
    resolved = await _openfigi_resolve(misses) if misses else {}
    for c in misses:
        hit = resolved.get(c)
        if hit and hit.get("ticker"):
            store.cache_put_ticker(c, hit["ticker"], hit.get("name"), None, "mapped")
            cache_hits[c] = {"ticker": hit["ticker"], "name": hit.get("name"), "sector": None}
        else:
            store.cache_put_ticker(c, None, None, None, "unmapped")
            cache_hits[c] = {"ticker": None, "sector": None}

    # 3. Enrich holdings
    for h in holdings:
        c = (h.get("cusip") or "").strip()
        info = cache_hits.get(c, {})
        ticker = info.get("ticker")
        out = h.copy()
        out["ticker"] = ticker
        out["sector"] = info.get("sector") or "Unknown"
        out["mapping_status"] = "mapped" if ticker else "unmapped"
        enriched.append(out)

    mapped_count = sum(1 for h in enriched if h.get("ticker"))
    hub_add_note(
        "data_ingest",
        f"[Security Mapper] Mapped {mapped_count}/{len(enriched)} holdings to tickers "
        f"({len(cache_hits) - len(misses)} cache hits, {len(misses)} OpenFIGI lookups)",
    )
    return enriched
