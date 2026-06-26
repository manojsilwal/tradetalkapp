"""
Deterministic explanation generator (Plan §10) for the Picks & Shovels finder.

Anti-hallucination rules (Plan §18) are enforced structurally:
  - a financial fact is only emitted when its source field is non-null, else the
    field is reported as "Not available";
  - every explanation includes at least one risk;
  - language uses "selected for research" / "ranks highly" — never buy/sell/hold.

An optional LLM polish layer (``PICKS_SHOVELS_LLM_POLISH=1``) can rephrase the
deterministic narrative without introducing new numbers; it is never used in the
batch hot path and falls back to the deterministic text on any error.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import themes as _themes

_CYCLICAL_SECTORS = {
    "energy", "basic materials", "materials", "industrials",
    "consumer cyclical", "real estate",
}


def llm_polish_enabled() -> bool:
    return os.environ.get("PICKS_SHOVELS_LLM_POLISH", "0").strip() == "1"


def _fmt_pct(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    sign = "+" if v > 0 else ""
    return f"{sign}{round(float(v), 1)}%"


def _theme_labels(theme_ids: List[str]) -> str:
    labels = [_themes.theme_label(t) for t in theme_ids]
    return ", ".join(labels) if labels else "infrastructure supply chain"


def _financial_evidence(fund: Dict[str, Any], momo: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    rg = _fmt_pct(fund.get("revenue_growth_pct"))
    out.append(f"Revenue growth (YoY): {rg}" if rg else "Revenue growth (YoY): Not available")
    gm = _fmt_pct(fund.get("gross_margin_pct"))
    out.append(f"Gross margin: {gm}" if gm else "Gross margin: Not available")
    fcf = _fmt_pct(fund.get("fcf_yield_pct"))
    out.append(f"Free-cash-flow yield: {fcf}" if fcf else "Free-cash-flow yield: Not available")
    r3 = _fmt_pct(momo.get("ret_3m_pct"))
    out.append(f"3-month price return: {r3}" if r3 else "3-month price return: Not available")
    return out


def _demand_evidence(membership, evidence: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if membership is not None and membership.exposure_reason:
        out.append(membership.exposure_reason)
    if evidence and evidence.get("available") and evidence.get("demand_evidence"):
        out.extend([str(x) for x in evidence["demand_evidence"]])
    else:
        out.append("Demand evidence from news/filings/transcripts is pending ingestion (not yet available).")
    return out


def _risks(fund: Dict[str, Any], breakdown: Dict[str, Any], hiddenness: str) -> List[str]:
    risks: List[str] = []
    val = breakdown.get("valuation_risk_score")
    if val is not None and val < 55:
        risks.append("Valuation/risk is elevated relative to peers.")
    sector = str(fund.get("sector") or "").strip().lower()
    if sector in _CYCLICAL_SECTORS:
        risks.append("Demand is cyclical and may normalize.")
    if hiddenness == "Hidden Player":
        risks.append("Lower analyst/media coverage increases information risk.")
    # Standard caveats (qualitative, not fabricated data) — always keep >=3 when possible.
    risks.append("Customer concentration risk for picks-and-shovels suppliers.")
    risks.append("Margin expansion may be temporary if the demand cycle cools.")
    # de-dup while preserving order, guarantee at least one
    seen: set = set()
    deduped = [r for r in risks if not (r in seen or seen.add(r))]
    return deduped or ["General execution and demand-durability risk."]


def build_explanation(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the Plan §10.1 explanation JSON + the §10.2 narrative for one scored row.

    ``row`` is expected to carry: ticker, company_name, themes (ids), bottleneck_solved,
    final_score, hiddenness_level, confidence_level, score_breakdown, fundamentals,
    momentum, evidence.
    """
    ticker = str(row.get("ticker") or "").upper()
    fund = row.get("fundamentals") or {}
    momo = row.get("momentum") or {}
    evidence = row.get("evidence") or {}
    breakdown = row.get("score_breakdown") or {}
    theme_ids = row.get("themes") or []
    membership = _themes.membership_for(ticker)
    bottleneck = row.get("bottleneck_solved") or (membership.bottleneck_solved if membership else "")
    hiddenness = row.get("hiddenness_level") or "Secondary Player"
    confidence = row.get("confidence_level") or "Medium"

    financial_evidence = _financial_evidence(fund, momo)
    demand_evidence = _demand_evidence(membership, evidence)
    risks = _risks(fund, breakdown, hiddenness)

    theme_label_str = _theme_labels(theme_ids)
    rg = _fmt_pct(fund.get("revenue_growth_pct"))
    gm = _fmt_pct(fund.get("gross_margin_pct"))
    fcf = _fmt_pct(fund.get("fcf_yield_pct"))

    rev_summary = f"revenue growth of {rg}" if rg else "revenue growth that is not yet available"
    margin_summary = f"a gross margin of {gm}" if gm else "margin data that is not yet available"
    cash_summary = f"a free-cash-flow yield of {fcf}" if fcf else "cash-flow detail that is not yet available"
    risk_phrase = "; ".join(risks[:3])

    narrative = (
        f"{ticker} was selected for research because it ranks highly in the "
        f"{theme_label_str} theme and benefits from {bottleneck or 'a structural demand cycle'}.\n\n"
        f"The financial evidence is {rev_summary}, {margin_summary}, and {cash_summary}.\n\n"
        f"The demand evidence includes {demand_evidence[0]} "
        f"This suggests the company may be benefiting from a structural demand cycle "
        f"rather than only short-term price momentum.\n\n"
        f"The main risks are {risk_phrase}.\n\n"
        f"Overall, the stock is classified as a {hiddenness} with a confidence level of {confidence}."
    )

    return {
        "ticker": ticker,
        "company_name": row.get("company_name") or ticker,
        "final_score": row.get("final_score"),
        "hiddenness_level": hiddenness,
        "themes": [_themes.theme_label(t) for t in theme_ids],
        "why_selected": (
            f"Selected for research because it ranks highly in the {theme_label_str} "
            f"theme and is exposed to {bottleneck or 'a structural demand cycle'}."
        ),
        "bottleneck_solved": bottleneck,
        "financial_evidence": financial_evidence,
        "demand_evidence": demand_evidence,
        "valuation_snapshot": [
            "Compare forward valuation against revenue growth and cyclicality.",
            "Flag if EV/Sales or P/E sits far above the company's historical range.",
        ],
        "risks": risks,
        "confidence_level": confidence,
        "narrative": narrative,
    }


async def polish_explanation(narrative: str, *, label: str = "picks_shovels") -> str:
    """Optional LLM rephrase (no new numbers). Falls back to the input on any error."""
    if not llm_polish_enabled():
        return narrative
    try:
        from ..deps import llm_client

        polished = await llm_client.generate_rag_polish(label, narrative)
        return polished or narrative
    except Exception:
        return narrative
