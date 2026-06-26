"""
CORAL Hub Skill: SEC 13F Universe Discovery

Auto-discovers the universe of institutional managers that file Form 13F-HR and
ranks them by AUM (sum of reported 13F market value) so the leaderboard can pick
the largest filers without a hardcoded seed list.

Pipeline:
1. Pull the SEC quarterly ``master.idx`` for the most recent quarter and collect
   every CIK that filed a 13F-HR / 13F-HR/A.
2. For each candidate (bounded by FUND_LB_DISCOVERY_MAX), fetch their latest 13F
   and sum market value to approximate AUM.
3. Return the top ``universe_size`` filers sorted by AUM.

All HTTP access respects SEC fair-access guidelines (descriptive User-Agent,
bounded concurrency, gzip).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.coral_agents import hub_add_note
from backend.coral_skills.sec_13f_ingestion import ingest_manager_13f

logger = logging.getLogger(__name__)

# No explicit Host header — httpx derives it per-request (www vs data subdomain).
SEC_HEADERS = {
    "User-Agent": os.environ.get(
        "SEC_USER_AGENT", "TradeTalkApp contact@tradetalk.example.com"
    ),
    "Accept-Encoding": "gzip, deflate",
}

THIRTEEN_F_FORMS = {"13F-HR", "13F-HR/A"}

# Default request pacing — SEC allows ~10 req/s; we stay conservative.
_MAX_CONCURRENCY = int(os.environ.get("FUND_LB_SEC_CONCURRENCY", "4"))


def _candidate_quarters(lookback: int = 4) -> List[Tuple[int, int]]:
    """Most-recent-first (year, quarter) tuples to probe for a filing index."""
    today = date.today()
    q = (today.month - 1) // 3 + 1
    year = today.year
    out: List[Tuple[int, int]] = []
    for _ in range(lookback):
        out.append((year, q))
        q -= 1
        if q == 0:
            q = 4
            year -= 1
    return out


async def _fetch_master_index(year: int, quarter: int) -> Optional[str]:
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=SEC_HEADERS)
            if resp.status_code != 200:
                logger.info("[Universe] master.idx %s QTR%s -> HTTP %s", year, quarter, resp.status_code)
                return None
            return resp.text
    except Exception as e:
        logger.warning("[Universe] master.idx fetch failed %s QTR%s: %s", year, quarter, e)
        return None


def _parse_13f_filers(master_idx: str) -> List[Dict[str, str]]:
    """Parse pipe-delimited master.idx, return unique 13F-HR filers (cik, name)."""
    seen: Dict[str, Dict[str, str]] = {}
    for line in master_idx.splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, company, form_type, _date_filed, _filename = parts
        if form_type.strip() not in THIRTEEN_F_FORMS:
            continue
        cik = cik.strip()
        if not cik.isdigit():
            continue
        # Keep first occurrence (dedupe by CIK)
        if cik not in seen:
            seen[cik] = {"cik": cik, "name": company.strip()}
    return list(seen.values())


async def _aum_for_cik(cik: str, name: str, sem: asyncio.Semaphore) -> Dict[str, Any]:
    """Fetch latest 13F for a CIK and sum market value as an AUM proxy."""
    async with sem:
        result = await ingest_manager_13f(cik, fund_id=cik)
    if result.get("status") != "success":
        return {"cik": cik, "name": name, "aum_usd": 0.0, "status": result.get("status")}
    holdings = result.get("holdings", [])
    # SEC 13F values are reported in whole dollars in modern XML; sum as-is.
    aum = sum((h.get("market_value_usd") or 0.0) for h in holdings)
    return {
        "cik": cik,
        "name": name,
        "aum_usd": aum,
        "report_period": result.get("report_period"),
        "status": "success",
        "holdings_count": len(holdings),
    }


async def discover_top_filers(
    universe_size: int = 150,
    discovery_max: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Discover the largest 13F filers by AUM.

    Args:
        universe_size: how many top-AUM managers to return.
        discovery_max: cap on how many filers to evaluate for AUM (0/None = env
            default FUND_LB_DISCOVERY_MAX, where 0 means evaluate all — slow).
    """
    if discovery_max is None:
        discovery_max = int(os.environ.get("FUND_LB_DISCOVERY_MAX", "300"))

    master_idx = None
    chosen_quarter = None
    for (year, quarter) in _candidate_quarters():
        master_idx = await _fetch_master_index(year, quarter)
        if master_idx:
            filers_preview = _parse_13f_filers(master_idx)
            if filers_preview:
                chosen_quarter = (year, quarter)
                break

    if not master_idx or not chosen_quarter:
        hub_add_note("data_ingest", "[Universe] No SEC 13F index available in lookback window")
        return []

    filers = _parse_13f_filers(master_idx)
    logger.info(
        "[Universe] %s QTR%s: %d unique 13F filers discovered",
        chosen_quarter[0], chosen_quarter[1], len(filers),
    )

    if discovery_max and discovery_max > 0:
        candidates = filers[:discovery_max]
    else:
        candidates = filers

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    tasks = [_aum_for_cik(f["cik"], f["name"], sem) for f in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    scored: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, dict) and r.get("aum_usd", 0) > 0:
            scored.append(r)

    scored.sort(key=lambda x: x["aum_usd"], reverse=True)
    top = scored[:universe_size]

    hub_add_note(
        "data_ingest",
        f"[Universe] Evaluated {len(candidates)} filers, ranked top {len(top)} by AUM "
        f"(quarter {chosen_quarter[0]} Q{chosen_quarter[1]})",
    )
    return top
