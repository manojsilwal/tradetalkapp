"""
NR-6 — thematic ETF/fund product-pipeline detection via SEC EDGAR full-text search.

Wall Street "productizes" a narrative by filing thematic funds (N-1A) and
registration statements (S-1) before the marketing push. Multiple issuers filing
around the same theme is the manufacturing signal (Plan §5.1, §7.3).

Uses the EDGAR full-text search JSON API (``efts.sec.gov``) with the SEC
fair-access User-Agent. Best-effort + flag-gated (``NARRATIVE_RADAR_PRODUCTIZATION``):
any failure degrades to ``{"available": False}`` so the scan never hangs/breaks.

The HTTP fetch is isolated in ``_search_count`` so the scoring inputs builder
(``theme_productization``) can be unit-tested by injecting a fake counter.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_FTS_URL = "https://efts.sec.gov/LATEST/search-index?q={q}&forms={forms}"
_FORMS = "N-1A,S-1,485APOS,485BPOS"


def enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_PRODUCTIZATION", "0").strip() == "1"


def _user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", "TradeTalkApp contact@tradetalk.example.com").strip()


def _timeout_s() -> float:
    return float(os.environ.get("NARRATIVE_RADAR_PRODUCTIZATION_TIMEOUT_S", "8") or "8")


def _search_count(phrase: str) -> Dict[str, Any]:
    """Return {total, issuers} for a quoted phrase across thematic-fund forms."""
    q = urllib.parse.quote(f'"{phrase}"')
    url = _FTS_URL.format(q=q, forms=urllib.parse.quote(_FORMS))
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_timeout_s()) as resp:  # noqa: S310 (trusted SEC host)
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    hits = (data or {}).get("hits", {})
    total = ((hits.get("total") or {}).get("value")) or 0
    issuers: set = set()
    for h in hits.get("hits", []) or []:
        src = h.get("_source") or {}
        for name in (src.get("display_names") or []):
            issuers.add(str(name).split("(")[0].strip().upper())
    return {"total": int(total), "issuers": len(issuers)}


def theme_productization(
    keywords: Sequence[str],
    *,
    counter: Optional[Callable[[str], Dict[str, Any]]] = None,
    max_keywords: int = 4,
) -> Dict[str, Any]:
    """
    Build the productization signal for a theme from its keyword dictionary.

    ``counter`` defaults to the live EDGAR search; tests inject a fake. Returns the
    ``signals['productization']`` shape consumed by ``scoring.productization_score``.
    """
    fn = counter or _search_count
    total = 0
    issuer_max = 0
    used = 0
    for kw in list(keywords)[:max_keywords]:
        try:
            res = fn(kw)
        except Exception as e:
            logger.debug("[NarrativeRadar] ETF filing search failed for %r: %s", kw, e)
            continue
        total += int(res.get("total") or 0)
        issuer_max = max(issuer_max, int(res.get("issuers") or 0))
        used += 1
    if used == 0:
        return {"available": False}
    return {
        "available": True,
        "filings_count": total,
        "issuer_count": issuer_max,
        "aum_growth_pct": None,        # requires an AUM feed (Plan §5.3) — not fabricated
        "launch_after_runup": None,    # derived once launch dates + price runs are wired
    }


def build_theme_productization(keywords: Sequence[str]) -> Dict[str, Any]:
    """Live entry point (flag-gated). Resilient → unavailable on any failure."""
    if not enabled():
        return {"available": False}
    try:
        return theme_productization(keywords)
    except Exception as e:
        logger.debug("[NarrativeRadar] productization build failed: %s", e)
        return {"available": False}
