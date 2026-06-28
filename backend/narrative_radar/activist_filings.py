"""
Weeks-fresh SC 13D / 13G activist stake signal for the Narrative Rotation Radar.

Uses the SEC EDGAR current-filings Atom feed (same pattern as ``sec_filing_job``)
to detect issuers with recent activist / large-holder disclosures. Flag-gated and
resilient — degrades to ``{"available": False}`` on any failure.
"""
from __future__ import annotations

import logging
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Sequence, Set

from ..actionable_companies import _linscore
from ..connectors.backtest_data import _CIK_MAP, _load_cik_map

logger = logging.getLogger(__name__)

_ACTIVIST_FORMS = ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A")
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_DEFAULT_WINDOW_DAYS = 56  # ~8 weeks


def _activist_enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_ACTIVIST", "1").strip() != "0"


def _user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", "TradeTalkApp contact@tradetalk.example.com").strip()


def parse_atom_activist_tickers(
    xml_bytes: bytes,
    *,
    cik_to_ticker: Dict[str, str],
    cutoff: datetime,
) -> Set[str]:
    """Extract issuer tickers from an SEC Atom feed body."""
    out: Set[str] = set()
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return out
    for entry in root.findall("atom:entry", _ATOM_NS):
        title_elem = entry.find("atom:title", _ATOM_NS)
        updated_elem = entry.find("atom:updated", _ATOM_NS)
        if title_elem is None or updated_elem is None:
            continue
        title = title_elem.text or ""
        updated_str = updated_elem.text or ""
        m = re.search(r"\((\d+)\)", title)
        if not m:
            continue
        cik = m.group(1).zfill(10)
        ticker = cik_to_ticker.get(cik)
        if not ticker:
            continue
        try:
            dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            if dt >= cutoff:
                out.add(ticker.upper())
        except Exception:
            out.add(ticker.upper())
    return out


def fetch_recent_activist_tickers(*, days: int = _DEFAULT_WINDOW_DAYS) -> Set[str]:
    """Sync fetch of recent SC 13D/G issuer tickers from SEC Atom feeds."""
    if not _activist_enabled():
        return set()
    try:
        _load_cik_map()
        cik_to_ticker = {cik: ticker for ticker, cik in _CIK_MAP.items()}
    except Exception as e:
        logger.debug("[NarrativeRadar] activist CIK map failed: %s", e)
        return set()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    tickers: Set[str] = set()
    for form in _ACTIVIST_FORMS:
        form_q = form.replace(" ", "+")
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
            f"&type={form_q}&count=100&output=atom"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
            with urllib.request.urlopen(req, timeout=15) as response:
                content = response.read()
            tickers |= parse_atom_activist_tickers(
                content, cik_to_ticker=cik_to_ticker, cutoff=cutoff
            )
        except Exception as e:
            logger.debug("[NarrativeRadar] activist feed %s failed: %s", form, e)
            continue
    return tickers


def activist_signal_8w(
    members: Sequence[str],
    *,
    recent_tickers: Optional[Set[str]] = None,
    sample_k: int = 8,
) -> Dict[str, object]:
    """
    Score basket members with recent SC 13D/G disclosures (weeks-fresh activist interest).
  """
    if not _activist_enabled():
        return {"available": False}
    basket = [m.upper() for m in members[:sample_k] if m]
    if not basket:
        return {"available": False}
    try:
        hits_set = recent_tickers if recent_tickers is not None else fetch_recent_activist_tickers()
    except Exception as e:
        logger.debug("[NarrativeRadar] activist fetch failed: %s", e)
        return {"available": False}
    if not hits_set:
        return {"available": False}
    hits = [tk for tk in basket if tk in hits_set]
    if not hits:
        return {"available": False}
    hit_ratio = len(hits) / len(basket)
    score = round(_linscore(hit_ratio, 0.0, 0.5), 2)
    return {
        "available": True,
        "activist_filing_count": len(hits),
        "activist_hit_tickers": hits,
        "activist_score": score,
    }
