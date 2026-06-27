"""
CORAL Hub Skill: Security Mapping & Sector Agent

Responsible for:
- Mapping CUSIPs or Issuer Names to standard Tickers using yfinance / internal master.
- Fetching sector and industry classifications from yfinance.
- Flagging unmapped or ambiguous securities.
"""
import asyncio
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
import yfinance as yf
import httpx
from typing import Dict, Any, List, Optional

from backend.coral_agents import hub_add_note

logger = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

_STATIC_CUSIP_PATH = Path(__file__).resolve().parent.parent / "data" / "cusip_ticker_static.json"


@lru_cache(maxsize=1)
def _static_cusip_map() -> Dict[str, Dict[str, Any]]:
    """Bundled CUSIP -> {ticker, name, sector} map (OpenFIGI bootstrap output)."""
    try:
        raw = json.loads(_STATIC_CUSIP_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.info("[Security Mapper] static CUSIP map unavailable (%s): %s", _STATIC_CUSIP_PATH, e)
        return {}
    # Keep only rows that actually carry a ticker.
    return {
        str(c).strip(): v
        for c, v in raw.items()
        if isinstance(v, dict) and v.get("ticker")
    }

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


async def _openfigi_post(client: httpx.AsyncClient, batch: List[str], delay: float) -> Optional[list]:
    """POST one CUSIP batch to OpenFIGI, retrying on 429 / transient errors.

    Returns the parsed JSON list on success, or ``None`` if the batch could not
    be resolved after retries (caller marks those CUSIPs unresolved).
    """
    payload = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch]
    for attempt in range(3):
        try:
            resp = await client.post(OPENFIGI_URL, headers=_openfigi_headers(), json=payload)
            if resp.status_code == 429:
                backoff = max(delay, 10.0) * (attempt + 1)
                logger.warning("[Security Mapper] OpenFIGI rate-limited; retry in %.0fs", backoff)
                await asyncio.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[Security Mapper] OpenFIGI batch attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(delay * (attempt + 1))
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
            results = await _openfigi_post(client, batch, delay)
            if results is None:
                for c in batch:
                    out.setdefault(c, None)
            else:
                for cusip, item in zip(batch, results):
                    if isinstance(item, dict) and item.get("data"):
                        out[cusip] = _pick_ticker(item["data"])
                    else:
                        out[cusip] = None

            if start + batch_size < len(cusips):
                await asyncio.sleep(delay)
    return out


def _enrich_sector(ticker: Optional[str], existing_sector: Optional[str]) -> str:
    """Resolve a sector for a ticker from the bundled references (no network)."""
    if not ticker:
        return existing_sector or "Unknown"
    from backend import ticker_reference

    meta = ticker_reference.get_ticker_meta(ticker)
    if meta and meta.get("sector") and meta["sector"] != "Unknown":
        return meta["sector"]
    try:
        from backend.sp500_gics_reference import get_sp500_gics

        gics = get_sp500_gics(ticker)
        if gics and gics.get("sector"):
            return gics["sector"]
    except Exception:
        pass
    return existing_sector or (meta.get("sector") if meta else None) or "Unknown"


async def map_holdings_to_tickers(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Resolve a 13F holdings list (issuer_name + cusip) to tickers via a layered
    resolver, then enrich sector. Adds ``ticker``, ``sector``, and
    ``mapping_status`` keys to each holding.

    Resolution order per CUSIP:
      1. ``cusip_ticker_cache`` (persistent DB)
      2. bundled ``cusip_ticker_static.json``
      3. issuer-name fallback via the bundled ticker reference
      4. OpenFIGI (network) for remaining misses
    """
    from backend import fund_leaderboard_store as store
    from backend import ticker_reference

    static_map = _static_cusip_map()

    unique_cusips = sorted({(h.get("cusip") or "").strip() for h in holdings if (h.get("cusip") or "").strip()})

    # cusip -> {ticker, name, sector, status}
    resolved: Dict[str, Dict[str, Any]] = {}
    n_cache = n_static = n_issuer = 0
    openfigi_misses: List[str] = []

    # 1. DB cache + 2. bundled static map
    for c in unique_cusips:
        cached = store.cache_get_ticker(c)
        if cached and cached.get("ticker"):
            resolved[c] = {**cached, "status": "mapped"}
            n_cache += 1
            continue
        if cached and cached.get("mapping_status") == "unmapped":
            # Previously confirmed miss — still try static/issuer/openfigi below,
            # but do not count as a cache hit.
            pass
        s = static_map.get(c)
        if s and s.get("ticker"):
            resolved[c] = {"ticker": s["ticker"], "name": s.get("name"), "sector": s.get("sector"), "status": "mapped_static"}
            n_static += 1
            continue
        openfigi_misses.append(c)

    # 3. Issuer-name fallback for CUSIP misses that carry an issuer name.
    cusip_to_issuer: Dict[str, str] = {}
    for h in holdings:
        c = (h.get("cusip") or "").strip()
        if c and c not in cusip_to_issuer:
            cusip_to_issuer[c] = h.get("issuer_name") or ""

    still_missing: List[str] = []
    for c in openfigi_misses:
        issuer = cusip_to_issuer.get(c, "")
        ticker = ticker_reference.lookup_by_issuer_name(issuer) if issuer else None
        if ticker:
            resolved[c] = {"ticker": ticker, "name": issuer, "sector": None, "status": "mapped_issuer"}
            n_issuer += 1
        else:
            still_missing.append(c)

    # 4. OpenFIGI for the remainder, persisting hits + confirmed misses to cache.
    figi = await _openfigi_resolve(still_missing) if still_missing else {}
    n_openfigi = 0
    for c in still_missing:
        hit = figi.get(c)
        if hit and hit.get("ticker"):
            resolved[c] = {"ticker": hit["ticker"], "name": hit.get("name"), "sector": None, "status": "mapped_openfigi"}
            n_openfigi += 1
        else:
            store.cache_put_ticker(c, None, None, None, "unmapped")

    # Persist newly resolved tickers (static/issuer/openfigi) into the cache.
    for c, info in resolved.items():
        if info.get("status") in ("mapped_static", "mapped_issuer", "mapped_openfigi"):
            store.cache_put_ticker(c, info["ticker"], info.get("name"), info.get("sector"), "mapped")

    # Enrich + emit.
    enriched: List[Dict[str, Any]] = []
    for h in holdings:
        c = (h.get("cusip") or "").strip()
        info = resolved.get(c, {})
        ticker = info.get("ticker")
        out = h.copy()
        out["ticker"] = ticker
        out["sector"] = _enrich_sector(ticker, info.get("sector"))
        out["mapping_status"] = info.get("status", "unmapped") if ticker else "unmapped"
        enriched.append(out)

    mapped_count = sum(1 for h in enriched if h.get("ticker"))
    hub_add_note(
        "data_ingest",
        f"[Security Mapper] Mapped {mapped_count}/{len(enriched)} holdings "
        f"(cache={n_cache}, static={n_static}, issuer={n_issuer}, openfigi={n_openfigi}, "
        f"unresolved={len(still_missing) - n_openfigi})",
    )
    return enriched
