"""
Phase-3 demand-evidence engine for the Picks & Shovels Momentum Finder (Plan §7.6).

Pulls per-ticker demand / bottleneck signals from public sources and scores them
**deterministically** with curated keyword sets — no fabricated numbers (Plan §18).
Every network path is wrapped so a failure degrades to ``{"available": False}`` and
the scorer falls back to a neutral component instead of inventing evidence.

Sources
-------
- Google News RSS   (default on; reuses the connectors/news_scanner request shape)
- SEC filing text    (opt-in ``PICKS_SHOVELS_FILING_EVIDENCE=1``; latest 10-Q/10-K/8-K)

The returned dict matches the inputs ``scoring.bottleneck_evidence_score`` expects:
``positive_keyword_score``, ``negative_keyword_penalty``, ``news_catalyst_score``,
``filing_evidence_score`` — plus ``headlines`` / ``demand_evidence`` (real text only)
for the explanation layer.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Sequence

import requests
import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)

# ── Curated keyword sets (picks-and-shovels supply tightness) ─────────────────

PS_POSITIVE_KEYWORDS: List[str] = [
    "record demand", "record revenue", "record backlog", "backlog", "sold out",
    "capacity expansion", "expand capacity", "expanding capacity", "supply constraint",
    "supply shortage", "shortage", "constrained supply", "tight supply", "design win",
    "ramp", "ramping", "raised guidance", "raises guidance", "raise guidance",
    "boost guidance", "increased guidance", "strong demand", "robust demand",
    "surging demand", "demand surge", "accelerating demand", "capacity buildout",
    "new facility", "new fab", "fab expansion", "hyperscaler", "data center",
    "ai demand", "bookings", "order backlog", "fully booked", "multi-year agreement",
    "long-term agreement", "capex increase", "capital expenditure", "capex surge",
]

PS_NEGATIVE_KEYWORDS: List[str] = [
    "demand weakness", "weak demand", "softening demand", "demand slowdown",
    "inventory correction", "excess inventory", "oversupply", "glut",
    "cut guidance", "cuts guidance", "lowered guidance", "guidance cut",
    "slowdown", "downturn", "cancellation", "cancelled orders", "order cancellation",
    "push out", "pushout", "missed estimates", "layoffs", "plant closure",
    "pricing pressure", "margin pressure",
]

# Hard, timely catalysts (a specific demand event worth surfacing).
PS_CATALYST_KEYWORDS: List[str] = [
    "record", "backlog", "sold out", "shortage", "capacity", "design win",
    "raised guidance", "raises guidance", "expansion", "new fab", "new facility",
    "bookings",
]

NEUTRAL = 50.0


# ── Config knobs ─────────────────────────────────────────────────────────────


def news_evidence_enabled() -> bool:
    return os.environ.get("PICKS_SHOVELS_NEWS_EVIDENCE", "1").strip() != "0"


def filing_evidence_enabled() -> bool:
    return os.environ.get("PICKS_SHOVELS_FILING_EVIDENCE", "0").strip() == "1"


def _news_max_items() -> int:
    return max(3, int(os.environ.get("PICKS_SHOVELS_NEWS_MAX_ITEMS", "12") or "12"))


def _news_timeout_s() -> float:
    return float(os.environ.get("PICKS_SHOVELS_NEWS_TIMEOUT_S", "6") or "6")


# ── Deterministic keyword scoring (pure, offline-testable) ────────────────────


def _count_hits(text: str, keywords: Sequence[str]) -> int:
    t = (text or "").lower()
    return sum(1 for kw in keywords if kw in t)


def score_keywords(texts: Sequence[str]) -> Dict[str, Any]:
    """
    Score a list of short texts (headlines + snippets) against the keyword sets.

    Returns the bounded sub-scores ``bottleneck_evidence_score`` consumes plus raw
    hit counts and the matched texts. Pure: no I/O, fully deterministic.
    """
    pos_total = 0
    neg_total = 0
    catalysts: List[str] = []
    matched: List[str] = []
    for text in texts:
        if not text:
            continue
        p = _count_hits(text, PS_POSITIVE_KEYWORDS)
        n = _count_hits(text, PS_NEGATIVE_KEYWORDS)
        c = _count_hits(text, PS_CATALYST_KEYWORDS)
        pos_total += p
        neg_total += n
        if p > 0 and p >= n:
            matched.append(text.strip())
        if c > 0:
            catalysts.append(text.strip())

    # Map raw counts onto bounded sub-scores (diminishing returns), so the final
    # bottleneck score (NEUTRAL + pos - neg + catalyst + filing) stays in 0-100.
    return {
        "positive_keyword_score": round(min(40.0, 8.0 * pos_total), 2),
        "negative_keyword_penalty": round(min(40.0, 10.0 * neg_total), 2),
        "news_catalyst_score": round(min(20.0, 5.0 * len(catalysts)), 2),
        "positive_hits": pos_total,
        "negative_hits": neg_total,
        "matched": matched[:6],
    }


# ── Google News RSS (per ticker) ─────────────────────────────────────────────


def _google_news_rss(query: str, *, max_items: int, timeout: float) -> List[Dict[str, str]]:
    url = (
        "https://news.google.com/rss/search?q="
        + requests.utils.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    resp = requests.get(
        url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (TradeTalk PicksShovels)"}
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    items: List[Dict[str, str]] = []
    if channel is None:
        return items
    for item in channel.findall("item")[:max_items]:
        items.append({
            "title": item.findtext("title", "") or "",
            "link": item.findtext("link", "") or "",
            "pub_date": item.findtext("pubDate", "") or "",
            "source": item.findtext("source", "") or "",
            "snippet": (item.findtext("description", "") or "")[:300],
        })
    return items


def fetch_news_evidence(ticker: str, company_name: str = "") -> Dict[str, Any]:
    """Per-ticker Google News RSS demand evidence. ``{"available": False}`` on failure."""
    ticker = (ticker or "").upper()
    q_name = (company_name or "").strip() or ticker
    query = f"{q_name} {ticker} (demand OR backlog OR capacity OR guidance OR revenue)"
    try:
        items = _google_news_rss(query, max_items=_news_max_items(), timeout=_news_timeout_s())
    except Exception as e:  # network/parse — degrade, never raise
        logger.debug("[PicksShovels] news fetch failed for %s: %s", ticker, e)
        return {"available": False}
    if not items:
        return {"available": False}

    scored = score_keywords([f"{it['title']} {it['snippet']}" for it in items])

    # Human-readable evidence = the actual headline titles that matched (real text).
    demand_evidence: List[str] = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        if _count_hits(f"{title} {it.get('snippet', '')}", PS_POSITIVE_KEYWORDS) > 0:
            src = (it.get("source") or "").strip()
            demand_evidence.append(f"{title} — {src}" if src else title)
        if len(demand_evidence) >= 5:
            break

    return {
        "available": True,
        "positive_keyword_score": scored["positive_keyword_score"],
        "negative_keyword_penalty": scored["negative_keyword_penalty"],
        "news_catalyst_score": scored["news_catalyst_score"],
        "headlines": items,
        "demand_evidence": demand_evidence,
    }


# ── SEC filing text (opt-in) ─────────────────────────────────────────────────


def fetch_filing_evidence(ticker: str) -> Dict[str, Any]:
    """Latest 10-Q/10-K/8-K keyword scan. Opt-in; ``{"available": False}`` on failure."""
    if not filing_evidence_enabled():
        return {"available": False}
    try:
        from ..connectors.backtest_data import _ticker_to_cik, _edgar_get

        cik = _ticker_to_cik(ticker)
        if not cik:
            return {"available": False}
        sub = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=15)
        sub.raise_for_status()
        recent = (sub.json().get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accns = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []

        target = None
        for form, accn, doc in zip(forms, accns, docs):
            if form in ("10-Q", "10-K", "8-K") and accn and doc:
                target = (form, accn, doc)
                break
        if not target:
            return {"available": False}

        form, accn, doc = target
        cik_int = str(int(cik))
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn.replace('-', '')}/{doc}"
        resp = _edgar_get(url, timeout=20)
        resp.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", resp.text)[:200_000]  # strip tags, bound size
        scored = score_keywords([text])
        filing_score = max(
            0.0,
            round(min(20.0, 3.0 * scored["positive_hits"] - 3.0 * scored["negative_hits"]), 2),
        )
        return {"available": True, "filing_evidence_score": filing_score, "filing_form": form}
    except Exception as e:  # CIK/network/parse — degrade, never raise
        logger.debug("[PicksShovels] filing evidence failed for %s: %s", ticker, e)
        return {"available": False}


# ── Merge ────────────────────────────────────────────────────────────────────


def fetch_demand_evidence(ticker: str, company_name: str = "") -> Dict[str, Any]:
    """Merge news + (optional) filing evidence into one scorer-ready dict."""
    news = fetch_news_evidence(ticker, company_name) if news_evidence_enabled() else {"available": False}
    filing = fetch_filing_evidence(ticker)  # internally no-ops when disabled

    if not news.get("available") and not filing.get("available"):
        return {"available": False, "demand_evidence": []}

    return {
        "available": True,
        "positive_keyword_score": float(news.get("positive_keyword_score") or 0.0) if news.get("available") else 0.0,
        "negative_keyword_penalty": float(news.get("negative_keyword_penalty") or 0.0) if news.get("available") else 0.0,
        "news_catalyst_score": float(news.get("news_catalyst_score") or 0.0) if news.get("available") else 0.0,
        "filing_evidence_score": float(filing.get("filing_evidence_score") or 0.0) if filing.get("available") else 0.0,
        "demand_evidence": list(news.get("demand_evidence") or []),
        "headlines": list(news.get("headlines") or []),
        "sources": {"news": bool(news.get("available")), "filing": bool(filing.get("available"))},
    }
