"""Canonical Polymarket event relevance gating for equity analysis surfaces."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_EQUITY_CONTEXT_TERMS = (
    "stock",
    "stocks",
    "share",
    "shares",
    "equity",
    "earnings",
    "eps",
    "revenue",
    "guidance",
    "market cap",
    "ipo",
    "split",
    "dividend",
    "nasdaq",
    "nyse",
    "s&p",
    "buyout",
    "merger",
    "acquisition",
    "takeover",
    "valuation",
    "price target",
)

_NOISE_TERMS = (
    "election",
    "president",
    "senate",
    "house of representatives",
    "governor",
    "poll",
    "presidential",
    "oscar",
    "super bowl",
    "world cup",
)

GATE_THRESHOLD = 0.45


def score_polymarket_relevance(
    title: str,
    description: str,
    ticker: str,
    company_tokens: List[str],
) -> float:
    """0–1 score: company/ticker anchor + equity language; penalize political/sports noise."""
    blob = f"{title} {description}".lower()
    t = ticker.upper()
    score = 0.0
    toks = [x for x in company_tokens if x and len(x) >= 2]
    for tok in toks:
        if tok.lower() in blob:
            score += 0.34
            break
    if t.lower() in blob:
        score += 0.22
    for term in _EQUITY_CONTEXT_TERMS:
        if term in blob:
            score += 0.06
    for term in _NOISE_TERMS:
        if term in blob:
            score -= 0.2
    return max(0.0, min(1.0, score))


def company_tokens_from_name(company_name: str) -> List[str]:
    parts = re.split(r"[^\w]+", str(company_name or ""))
    return [p for p in parts if len(p) >= 2][:4]


@dataclass(frozen=True)
class GatedPolymarketEvent:
    title: str
    probability_pct: float
    relevance_score: float
    event: Dict[str, Any]


def select_gated_polymarket_event(
    events: List[Dict[str, Any]],
    ticker: str,
    company_tokens: List[str],
    *,
    threshold: float = GATE_THRESHOLD,
) -> Optional[GatedPolymarketEvent]:
    """Return best event with relevance >= threshold, or None."""
    best_ev: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for ev in events or []:
        title = str(ev.get("title") or "")
        desc = str(ev.get("description") or "")
        score = score_polymarket_relevance(title, desc, ticker, company_tokens)
        if score > best_score:
            best_score = score
            best_ev = ev
    if not best_ev or best_score < threshold:
        return None
    prob = best_ev.get("probability") or best_ev.get("yes_probability")
    try:
        prob_f = float(prob) if prob is not None else 50.0
        if prob_f <= 1.0:
            prob_f *= 100.0
    except (TypeError, ValueError):
        prob_f = 50.0
    return GatedPolymarketEvent(
        title=str(best_ev.get("title") or ""),
        probability_pct=round(prob_f, 1),
        relevance_score=round(best_score, 3),
        event=best_ev,
    )
