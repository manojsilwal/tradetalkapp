"""Small / micro cap growth-stage assessment endpoint."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from ..deps import llm_client, small_cap_metrics_connector

logger = logging.getLogger(__name__)
from ..schemas import (
    SmallCapAssessment,
    SmallCapMajorDeal,
    SmallCapRevenueStream,
    SmallCapSignal,
    SmallCapStreamYear,
)

router = APIRouter(tags=["small-cap"])

_SMALL_CAP_BUCKETS = frozenset({"Small Cap", "Micro Cap"})
_REQUIRED_LABELS = (
    "Profitability Runway",
    "Revenue & Margin Momentum",
    "Problem-Solution Fit",
    "Institutional Backing",
    "Founding Team Stability",
    "Product Moat",
)

_SMALL_CAP_MARKET_CAP_MAX = 2_000_000_000

_YF_TOTAL_REVENUE_SOURCE = "Yahoo Finance · annual income statement"
_YF_SEGMENT_REVENUE_SOURCE = "Yahoo Finance · income statement segment rows"


def _filing_sources(data: Dict[str, Any]) -> List[str]:
    filings: List[str] = []
    if data.get("fincrawler_sec_10k_excerpt"):
        filings.append("SEC 10-K")
    if data.get("fincrawler_sec_10q_excerpt"):
        filings.append("SEC 10-Q")
    return filings


def _llm_synthesis_source(data: Dict[str, Any], *, topic: str) -> str:
    filings = _filing_sources(data)
    if filings:
        return f"AI synthesis · Yahoo Finance + {' + '.join(filings)} (FinCrawler)"
    return f"AI synthesis · Yahoo Finance ({topic})"


def _news_item_source(headline: Dict[str, Any]) -> str:
    publisher = str(headline.get("publisher") or "").strip()
    raw_source = str(headline.get("source") or "").strip().lower()
    if raw_source in {"yfinance", "yahoo finance"}:
        label = "Yahoo Finance news"
    elif raw_source in {"fincrawler", "fincrawler_scrape"}:
        label = "FinCrawler news"
    elif raw_source:
        label = raw_source.replace("_", " ").title()
    else:
        label = "Yahoo Finance news"
    return f"{publisher} · {label}" if publisher else label


def _normalize_score(score: Any) -> str:
    s = str(score or "yellow").strip().lower()
    if s in {"green", "yellow", "red"}:
        return s
    return "yellow"


def _normalize_verdict(verdict: Any) -> str:
    v = str(verdict or "Watch").strip().title()
    if v in {"Compelling", "Watch", "Avoid"}:
        return v
    return "Watch"


def _build_heuristic_signals(data: Dict[str, Any]) -> List[SmallCapSignal]:
    """Offline-friendly fallback when LLM output is incomplete."""
    revenue_yoy = data.get("revenue_yoy") or []
    rev_growth = data.get("revenue_growth_yoy_pct")
    margins_q = data.get("gross_margins_quarterly") or []
    inst_pct = data.get("institutional_ownership_pct")
    inst_holders = data.get("institutional_holders") or []
    officers = data.get("officers") or []
    net_income = data.get("net_income")
    forward_eps = data.get("forward_eps")
    trailing_eps = data.get("trailing_eps")

    rev_scores = [p.get("yoy_growth_pct") for p in revenue_yoy if p.get("yoy_growth_pct") is not None]
    rev_improving = len(rev_scores) >= 2 and rev_scores[0] >= rev_scores[-1]
    margin_improving = (
        len(margins_q) >= 2
        and margins_q[0].get("margin_pct") is not None
        and margins_q[-1].get("margin_pct") is not None
        and margins_q[0]["margin_pct"] >= margins_q[-1]["margin_pct"]
    )

    runway_score = "yellow"
    if net_income is not None and net_income > 0:
        runway_score = "green"
    elif forward_eps is not None and forward_eps > 0:
        runway_score = "green"
    elif trailing_eps is not None and trailing_eps < 0 and (rev_growth or 0) > 15:
        runway_score = "yellow"
    elif trailing_eps is not None and trailing_eps < 0:
        runway_score = "red"

    momentum_score = "yellow"
    if rev_improving and margin_improving:
        momentum_score = "green"
    elif rev_growth is not None and rev_growth < 0:
        momentum_score = "red"

    inst_score = "yellow"
    if inst_pct is not None and inst_pct >= 25 and len(inst_holders) >= 2:
        inst_score = "green"
    elif inst_pct is not None and inst_pct < 5 and not inst_holders:
        inst_score = "red"

    team_score = "green" if len(officers) >= 2 else "yellow"
    founder_titles = " ".join((o.get("title") or "").lower() for o in officers)
    if not officers:
        team_score = "red"
    elif "founder" not in founder_titles and "chief executive" not in founder_titles:
        team_score = "yellow"

    officer_bits = [
        f"{(o.get('name') or '').strip()} ({(o.get('title') or '').strip()})"
        for o in officers[:3]
        if (o.get("name") or o.get("title"))
    ]
    team_detail = (
        f"Officers listed: {len(officers)} — {', '.join(officer_bits) or 'none'}."
    )

    return [
        SmallCapSignal(
            label="Profitability Runway",
            score=runway_score,
            headline="Profitability path depends on scaling vs. current losses.",
            detail=(
                f"Net income: {net_income}; forward EPS: {forward_eps}. "
                "Growth-stage names should show a credible 2-3 year route to profit, not a distant moonshot."
            ),
        ),
        SmallCapSignal(
            label="Revenue & Margin Momentum",
            score=momentum_score,
            headline="Revenue and margin trajectory needs sustained improvement.",
            detail=(
                f"Latest YoY revenue growth: {rev_growth}% with "
                f"{len(revenue_yoy)} annual comparison(s). Margin trend from quarterly gross margins."
            ),
        ),
        SmallCapSignal(
            label="Problem-Solution Fit",
            score="yellow",
            headline="Business summary suggests a real operating focus.",
            detail=(data.get("long_business_summary") or "Limited business description available.")[:320],
        ),
        SmallCapSignal(
            label="Institutional Backing",
            score=inst_score,
            headline="Institutional register indicates holder quality and conviction.",
            detail=(
                f"Institutional ownership: {inst_pct}%; "
                f"top holders: {', '.join(h.get('name', '') for h in inst_holders[:3]) or 'none listed'}."
            ),
        ),
        SmallCapSignal(
            label="Founding Team Stability",
            score=team_score,
            headline="Leadership bench visible from public officer roster.",
            detail=team_detail,
        ),
        SmallCapSignal(
            label="Product Moat",
            score="yellow",
            headline="Moat assessment requires product differentiation vs. peers.",
            detail=(
                f"Sector: {data.get('sector')}. Evaluate whether revenue growth reflects a defensible "
                "solution to a near-term bottleneck competitors cannot easily copy."
            ),
        ),
    ]


def _merge_llm_signals(llm_result: Dict[str, Any], data: Dict[str, Any]) -> List[SmallCapSignal]:
    raw_signals = llm_result.get("signals") if isinstance(llm_result.get("signals"), list) else []
    by_label: Dict[str, SmallCapSignal] = {}

    for item in raw_signals:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if label not in _REQUIRED_LABELS:
            continue
        by_label[label] = SmallCapSignal(
            label=label,
            score=_normalize_score(item.get("score")),
            headline=str(item.get("headline") or "Assessment pending.").strip(),
            detail=str(item.get("detail") or "").strip() or "No additional detail provided.",
        )

    fallback = {s.label: s for s in _build_heuristic_signals(data)}
    merged: List[SmallCapSignal] = []
    for label in _REQUIRED_LABELS:
        merged.append(by_label.get(label) or fallback[label])
    return merged


def _stream_year_from_dict(item: Any) -> Optional[SmallCapStreamYear]:
    if not isinstance(item, dict):
        return None
    year = str(item.get("year") or "").strip()
    if not year:
        return None
    return SmallCapStreamYear(
        year=year,
        revenue_usd=_num(item.get("revenue_usd")),
        gross_margin_pct=_num(item.get("gross_margin_pct")),
        operating_margin_pct=_num(item.get("operating_margin_pct")),
    )


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _baseline_revenue_streams(data: Dict[str, Any]) -> List[SmallCapRevenueStream]:
    streams: List[SmallCapRevenueStream] = []

    company_years = [
        _stream_year_from_dict(y)
        for y in (data.get("company_revenue_history_5y") or [])
    ]
    company_years = [y for y in company_years if y is not None]
    if company_years:
        streams.append(
            SmallCapRevenueStream(
                name="Total Company",
                years=company_years,
                source=_YF_TOTAL_REVENUE_SOURCE,
            )
        )

    for seg in data.get("segment_revenue_streams") or []:
        if not isinstance(seg, dict):
            continue
        years = [_stream_year_from_dict(y) for y in (seg.get("years") or [])]
        years = [y for y in years if y is not None]
        if not years:
            continue
        streams.append(
            SmallCapRevenueStream(
                name=str(seg.get("name") or "Segment").strip() or "Segment",
                years=years,
                source=_YF_SEGMENT_REVENUE_SOURCE,
            )
        )
    return streams


def _baseline_source_for_name(name: str, baseline: List[SmallCapRevenueStream]) -> Optional[str]:
    target = (name or "").strip().lower()
    for stream in baseline:
        if stream.name.strip().lower() == target and stream.source:
            return stream.source
    return None


def _normalize_revenue_streams(raw: Any, data: Dict[str, Any]) -> List[SmallCapRevenueStream]:
    baseline = _baseline_revenue_streams(data)
    if not isinstance(raw, list) or not raw:
        return baseline

    parsed: List[SmallCapRevenueStream] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        years = [_stream_year_from_dict(y) for y in (item.get("years") or [])]
        years = [y for y in years if y is not None]
        if not years:
            continue
        share = _num(item.get("latest_share_pct"))
        name = str(item.get("name") or "Revenue Stream").strip() or "Revenue Stream"
        source = _baseline_source_for_name(name, baseline) or _llm_synthesis_source(data, topic="revenue streams")
        parsed.append(
            SmallCapRevenueStream(
                name=name,
                latest_share_pct=share,
                years=sorted(years, key=lambda y: y.year)[-5:],
                source=source,
            )
        )

    return parsed if parsed else baseline


def _normalize_major_deals(raw: Any, data: Dict[str, Any]) -> List[SmallCapMajorDeal]:
    deals: List[SmallCapMajorDeal] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            partner = str(item.get("partner") or "").strip()
            if not partner:
                continue
            year_val = item.get("year")
            try:
                year_int = int(year_val) if year_val is not None else None
            except (TypeError, ValueError):
                year_int = None
            deals.append(
                SmallCapMajorDeal(
                    partner=partner,
                    deal_type=str(item.get("deal_type") or "partnership").strip() or "partnership",
                    amount_usd=_num(item.get("amount_usd")),
                    amount_label=str(item.get("amount_label") or "Undisclosed").strip() or "Undisclosed",
                    year=year_int,
                    summary=str(item.get("summary") or "").strip(),
                    predictability_note=str(item.get("predictability_note") or "").strip(),
                    source=_llm_synthesis_source(data, topic="news & filings"),
                )
            )

    if deals:
        return deals[:8]

    keywords = ("contract", "partnership", "deal", "agreement", "order", "award", "collaboration")
    seen_titles: set[str] = set()

    def _maybe_add(title: str, note: str = "", source: str = "") -> None:
        text = str(title or "").strip()
        if not text:
            return
        key = text.lower()[:120]
        if key in seen_titles:
            return
        lower = text.lower()
        if not any(k in lower for k in keywords):
            return
        seen_titles.add(key)
        deals.append(
            SmallCapMajorDeal(
                partner="See headline",
                deal_type="partnership",
                amount_label="Undisclosed",
                summary=text,
                predictability_note=note
                or "Potential revenue visibility — verify terms in primary filings or press release.",
                source=source or "News headline",
            )
        )

    for headline in data.get("news_headlines") or []:
        if isinstance(headline, dict):
            _maybe_add(str(headline.get("title") or ""), source=_news_item_source(headline))
        if len(deals) >= 5:
            return deals

    for summary in data.get("fincrawler_news_summaries") or []:
        _maybe_add(str(summary or ""), source="FinCrawler news")
        if len(deals) >= 5:
            return deals

    sec_8k = str(data.get("fincrawler_sec_8k_excerpt") or "").strip()
    if sec_8k and len(deals) < 5:
        for line in sec_8k.split("\n"):
            _maybe_add(
                line.strip(),
                "From recent 8-K filing excerpt — verify contract terms in the full filing.",
                source="SEC 8-K · FinCrawler",
            )
            if len(deals) >= 5:
                break

    return deals


@router.get("/small-cap-assessment/{ticker}", response_model=SmallCapAssessment)
async def get_small_cap_assessment(ticker: str) -> SmallCapAssessment:
    """
    Growth-stage assessment for Small Cap / Micro Cap names (< $2B market cap).
    Standard P/E-centric rules intentionally excluded.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="Ticker is required.")

    data = await small_cap_metrics_connector.fetch_data(ticker=sym)
    if data.get("error"):
        raise HTTPException(status_code=404, detail=f"Could not load data for {sym}: {data.get('error')}")

    market_cap = data.get("market_cap")
    cap_bucket = data.get("cap_bucket") or "Unknown"
    if market_cap is None or float(market_cap) >= _SMALL_CAP_MARKET_CAP_MAX:
        raise HTTPException(
            status_code=400,
            detail="Endpoint only applies to Small Cap and Micro Cap tickers (market cap < $2B).",
        )
    if cap_bucket not in _SMALL_CAP_BUCKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Cap bucket '{cap_bucket}' is not eligible for growth-stage assessment.",
        )

    llm_result = await llm_client.generate_small_cap_analysis(data)
    signals = _merge_llm_signals(llm_result if isinstance(llm_result, dict) else {}, data)

    overall_verdict = _normalize_verdict(
        llm_result.get("overall_verdict") if isinstance(llm_result, dict) else None
    )
    overall_rationale = str(
        (llm_result.get("overall_rationale") if isinstance(llm_result, dict) else "")
        or "Growth-stage framework applied — standard valuation metrics are not the primary lens."
    ).strip()

    revenue_streams = _normalize_revenue_streams(
        llm_result.get("revenue_streams") if isinstance(llm_result, dict) else None,
        data,
    )
    major_deals = _normalize_major_deals(
        llm_result.get("major_deals") if isinstance(llm_result, dict) else None,
        data,
    )

    assessment = SmallCapAssessment(
        ticker=sym,
        cap_bucket=cap_bucket,
        signals=signals,
        overall_verdict=overall_verdict,
        overall_rationale=overall_rationale,
        revenue_streams=revenue_streams,
        major_deals=major_deals,
    )
    _emit_small_cap_decision(sym, cap_bucket, data, assessment)
    return assessment


def _emit_small_cap_decision(
    sym: str,
    cap_bucket: str,
    data: Dict[str, Any],
    assessment: SmallCapAssessment,
) -> None:
    """Decision-Outcome Ledger emit (Phase F capture contract). Never raises."""
    try:
        from .. import decision_ledger as _dl
        from ..decision_ledger_registry import registry_attribution

        score_counts = {"green": 0, "yellow": 0, "red": 0}
        for s in assessment.signals:
            score_counts[s.score] = score_counts.get(s.score, 0) + 1
        features = [
            _dl.FeatureValue(name="cap_bucket", value_str=cap_bucket),
            _dl.FeatureValue(
                name="market_cap", value_num=_num(data.get("market_cap")),
            ),
            _dl.FeatureValue(
                name="revenue_growth_yoy_pct",
                value_num=_num(data.get("revenue_growth_yoy_pct")),
            ),
            _dl.FeatureValue(
                name="institutional_ownership_pct",
                value_num=_num(data.get("institutional_ownership_pct")),
            ),
            _dl.FeatureValue(name="green_signal_count", value_num=float(score_counts["green"])),
            _dl.FeatureValue(name="red_signal_count", value_num=float(score_counts["red"])),
        ]
        # RAG evidence: thread the same data-lake chunks an analyst would see.
        evidence = []
        try:
            from ..deps import knowledge_store

            _docs, refs = knowledge_store.query_with_refs(
                "stock_profiles",
                f"{sym} growth-stage small cap profile",
                n_results=2,
                where={"ticker": sym},
            )
            evidence = _dl.evidence_from_chunk_refs(refs, default_collection="stock_profiles")
        except Exception:
            evidence = []
        pv, snap, model = registry_attribution(roles=["small_cap_analyst"])
        _dl.emit_decision(
            decision_type="small_cap_assessment",
            symbol=sym,
            horizon_hint="21d",
            verdict=assessment.overall_verdict,
            output={
                "overall_verdict": assessment.overall_verdict,
                "overall_rationale": assessment.overall_rationale[:1000],
                "signal_scores": {s.label: s.score for s in assessment.signals},
            },
            source_route="backend/routers/small_cap.py::get_small_cap_assessment",
            features=features,
            evidence=evidence,
            prompt_versions=pv,
            registry_snapshot_id=snap,
            model=model,
        )
    except Exception as e:
        logger.debug("[SmallCap] ledger emit skipped: %s", e)
