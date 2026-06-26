"""
Picks-and-shovels momentum scoring (Plan §7, §8, §16) — pure, offline-testable.

The score is **cross-sectional**: component sub-scores that should rank a company
against its peers (price momentum, revenue growth, margins) are percentile-ranked
across the scanned universe — the one thing ``backend/momentum_model.py`` explicitly
defers. Components with no data available start at a neutral 50 (Plan §7.4) and the
final blend renormalizes over whichever components produced a value, so a company is
never scored against fabricated inputs (anti-hallucination, Plan §18).

Two-pass usage (see ``engine.py``):
    ctx = PercentileContext.build(raw_rows)
    scored = score_row(raw, ctx)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

# Reuse the deterministic metric helpers from the Actionable screener.
from ..actionable_companies import _avg, _clamp, _linscore

NEUTRAL = 50.0

# Final blend weights (Plan §7). Must sum to 1.0.
WEIGHTS: Dict[str, float] = {
    "price_momentum": 0.20,
    "revenue_acceleration": 0.20,
    "margin_expansion": 0.15,
    "backlog_rpo": 0.15,
    "customer_capex_exposure": 0.10,
    "bottleneck_evidence": 0.10,
    "valuation_risk": 0.10,
}

CYCLICAL_SECTORS = {
    "energy", "basic materials", "materials", "industrials",
    "consumer cyclical", "real estate",
}


# ── Cross-sectional percentile ranking ───────────────────────────────────────


def percentile_rank(value: Optional[float], population: Sequence[Optional[float]]) -> Optional[float]:
    """Percentile (0-100) of ``value`` within ``population`` (None-safe)."""
    if value is None:
        return None
    vals = sorted(float(v) for v in population if v is not None)
    if not vals:
        return None
    count = sum(1 for v in vals if v <= float(value))
    return round(count / len(vals) * 100.0, 2)


@dataclass
class PercentileContext:
    """Precomputed per-metric populations for cross-sectional ranking."""

    populations: Dict[str, List[float]]

    # metrics ranked cross-sectionally (path within the raw row)
    RANKED_METRICS = {
        "ret_3m": ("momentum", "ret_3m_pct"),
        "ret_6m": ("momentum", "ret_6m_pct"),
        "ret_12m": ("momentum", "ret_12m_pct"),
        "rev_growth": ("fundamentals", "revenue_growth_pct"),
        "eps_growth": ("fundamentals", "earnings_growth_pct"),
        "gross_margin": ("fundamentals", "gross_margin_pct"),
        "operating_margin": ("fundamentals", "operating_margin_pct"),
        "market_cap": ("fundamentals", "market_cap"),
    }

    @classmethod
    def build(cls, rows: Sequence[Dict[str, Any]]) -> "PercentileContext":
        pops: Dict[str, List[float]] = {k: [] for k in cls.RANKED_METRICS}
        for row in rows:
            for key, (section, field) in cls.RANKED_METRICS.items():
                v = (row.get(section) or {}).get(field)
                if v is not None:
                    try:
                        pops[key].append(float(v))
                    except (TypeError, ValueError):
                        pass
        return cls(populations=pops)

    def rank(self, metric: str, value: Optional[float]) -> Optional[float]:
        return percentile_rank(value, self.populations.get(metric, []))


# ── Weighted-blend helper (renormalize over present components) ───────────────


def _weighted(parts: Dict[str, Optional[float]]) -> Optional[float]:
    used = 0.0
    acc = 0.0
    for name, value in parts.items():
        if value is None:
            continue
        w = WEIGHTS[name]
        acc += w * value
        used += w
    if used == 0:
        return None
    return round(acc / used, 2)


def _blend(parts: List[tuple]) -> Optional[float]:
    """Blend ``[(weight, value), ...]`` renormalizing over present values."""
    used = 0.0
    acc = 0.0
    for w, v in parts:
        if v is None:
            continue
        acc += w * v
        used += w
    if used == 0:
        return None
    return _clamp(acc / used)


# ── Component scorers (each 0-100) ───────────────────────────────────────────


def price_momentum_score(momo: Dict[str, Any], ctx: PercentileContext) -> Optional[float]:
    """Plan §7.1 — percentile returns + trend confirmation + volume."""
    above_50 = _linscore(momo.get("above_50dma_pct"), -10, 10)
    above_200 = _linscore(momo.get("above_200dma_pct"), -10, 10)
    vol_conf = _linscore(momo.get("vol_ratio"), 0.8, 1.6)
    return _blend([
        (0.25, ctx.rank("ret_3m", momo.get("ret_3m_pct"))),
        (0.25, ctx.rank("ret_6m", momo.get("ret_6m_pct"))),
        (0.20, ctx.rank("ret_12m", momo.get("ret_12m_pct"))),
        (0.10, above_50),
        (0.10, above_200),
        (0.10, vol_conf),
    ])


def revenue_acceleration_score(fund: Dict[str, Any], ctx: PercentileContext) -> Optional[float]:
    """
    Plan §7.2. MVP: QoQ growth, sequential acceleration and guidance-revision are
    not available from yfinance ``.info`` (filled in Phase 2 via quarterly
    financials / SEC companyfacts), so this blends the YoY revenue-growth and
    earnings-growth percentiles. The unavailable inputs are reported by
    ``data_quality`` and never fabricated.
    """
    return _blend([
        (0.60, ctx.rank("rev_growth", fund.get("revenue_growth_pct"))),
        (0.40, ctx.rank("eps_growth", fund.get("earnings_growth_pct"))),
    ])


def margin_expansion_score(fund: Dict[str, Any], ctx: PercentileContext) -> Optional[float]:
    """
    Plan §7.3. MVP proxy: YoY/QoQ gross-margin *change* needs quarterly history
    (Phase 2). Until then we percentile-rank gross- and operating-margin *levels*
    as a pricing-power proxy (high, durable margins indicate pricing power).
    """
    return _blend([
        (0.60, ctx.rank("gross_margin", fund.get("gross_margin_pct"))),
        (0.40, ctx.rank("operating_margin", fund.get("operating_margin_pct"))),
    ])


def backlog_rpo_score(operating: Dict[str, Any]) -> float:
    """
    Plan §7.4. Backlog/RPO is not standardized and is not pulled in the MVP; per the
    plan we use a neutral 50 and let transcript/news evidence adjust later (Phase 3).
    """
    if not operating or not operating.get("available"):
        return NEUTRAL
    return _blend([
        (0.30, _linscore(operating.get("backlog_growth_pct"), -10, 40)),
        (0.25, _linscore(operating.get("rpo_growth_pct"), -10, 40)),
        (0.20, _linscore(operating.get("bookings_growth_pct"), -10, 40)),
        (0.15, _linscore(operating.get("book_to_bill"), 0.8, 1.4)),
        (0.10, 100.0 if operating.get("long_term_agreement") else 50.0),
    ]) or NEUTRAL


def customer_capex_exposure_score(theme: Dict[str, Any]) -> float:
    """Plan §7.5 — directness of exposure to customer capex cycles (theme seed)."""
    base = theme.get("customer_capex_seed")
    if base is None:
        return NEUTRAL
    return _clamp(float(base))


def bottleneck_evidence_score(evidence: Dict[str, Any]) -> float:
    """
    Plan §7.6. News/filing/transcript evidence is wired in Phase 3; until then this
    is neutral so a company is never ranked up on fabricated demand language.
    """
    if not evidence or not evidence.get("available"):
        return NEUTRAL
    pos = float(evidence.get("positive_keyword_score") or 0.0)
    neg = float(evidence.get("negative_keyword_penalty") or 0.0)
    news = float(evidence.get("news_catalyst_score") or 0.0)
    filing = float(evidence.get("filing_evidence_score") or 0.0)
    return _clamp(NEUTRAL + pos - neg + news + filing)


def valuation_risk_score(fund: Dict[str, Any]) -> float:
    """Plan §7.7 — starts at 70, rewards sane valuation / FCF / balance sheet."""
    score = 70.0
    fwd_pe = fund.get("forward_pe")
    rev_g = fund.get("revenue_growth_pct")
    if fwd_pe is not None and rev_g is not None and rev_g > 0:
        peg = float(fwd_pe) / max(float(rev_g), 1.0)
        if peg < 1.5:
            score += 10.0
        elif peg > 3.0:
            score -= 10.0
    fcf_yield = fund.get("fcf_yield_pct")
    if fcf_yield is not None:
        score += 10.0 if fcf_yield > 0 else -5.0
    de = fund.get("debt_to_equity")
    if de is not None:
        score += 5.0 if de < 1.0 else (-10.0 if de > 2.5 else 0.0)
    sector = str(fund.get("sector") or "").strip().lower()
    if sector in CYCLICAL_SECTORS:
        score -= 10.0
    return _clamp(score)


# ── Hiddenness (Plan §8) & confidence (Plan §16) ─────────────────────────────


def classify_hiddenness(
    market_cap: Optional[float],
    seed: str,
    ctx: Optional[PercentileContext] = None,
) -> Dict[str, Any]:
    """Big / Secondary / Hidden player + a 0-100 hiddenness score."""
    level = seed or ""
    if not level:
        if market_cap is None:
            level = "Secondary Player"
        elif market_cap >= 100e9:
            level = "Big Player"
        elif market_cap >= 10e9:
            level = "Secondary Player"
        else:
            level = "Hidden Player"
    # hiddenness_score: inverse market-cap percentile (smaller cap => more hidden)
    inv_cap = None
    if ctx is not None and market_cap is not None:
        pr = ctx.rank("market_cap", market_cap)
        inv_cap = (100.0 - pr) if pr is not None else None
    if inv_cap is None:
        inv_cap = {"Big Player": 15.0, "Secondary Player": 50.0, "Hidden Player": 85.0}[level]
    return {"hiddenness_level": level, "hiddenness_score": round(_clamp(inv_cap), 2)}


def confidence_level(coverage: float, evidence_sources: int) -> Dict[str, Any]:
    """Plan §16 — MVP weights data completeness + number of evidence sources."""
    completeness = _clamp(float(coverage) * 100.0)
    sources = _clamp(min(evidence_sources, 4) / 4.0 * 100.0)
    score = round(0.7 * completeness + 0.3 * sources, 2)
    if score >= 80:
        level = "High"
    elif score >= 55:
        level = "Medium"
    else:
        level = "Low"
    return {"confidence_score": score, "confidence_level": level}


# ── Final assembly ───────────────────────────────────────────────────────────


def score_row(raw: Dict[str, Any], ctx: PercentileContext) -> Dict[str, Any]:
    """Compute all component scores + final blended score for one company."""
    momo = raw.get("momentum") or {}
    fund = raw.get("fundamentals") or {}
    operating = raw.get("operating") or {}
    evidence = raw.get("evidence") or {}
    theme = raw.get("theme") or {}

    components: Dict[str, Optional[float]] = {
        "price_momentum": price_momentum_score(momo, ctx),
        "revenue_acceleration": revenue_acceleration_score(fund, ctx),
        "margin_expansion": margin_expansion_score(fund, ctx),
        "backlog_rpo": backlog_rpo_score(operating),
        "customer_capex_exposure": customer_capex_exposure_score(theme),
        "bottleneck_evidence": bottleneck_evidence_score(evidence),
        "valuation_risk": valuation_risk_score(fund),
    }

    final = _weighted(components)

    # Coverage: fraction of the *peer-ranked* inputs that were actually present.
    ranked_present = sum(
        1 for c in ("price_momentum", "revenue_acceleration", "margin_expansion")
        if components[c] is not None
    )
    coverage = round(ranked_present / 3.0, 3)

    evidence_sources = 0
    if evidence.get("available"):
        evidence_sources += 1
    if operating.get("available"):
        evidence_sources += 1
    if fund.get("market_cap") is not None:
        evidence_sources += 1

    market_cap = fund.get("market_cap")
    hidden = classify_hiddenness(market_cap, theme.get("hiddenness_seed") or "", ctx)
    conf = confidence_level(coverage, evidence_sources)

    rounded = {k: (round(v, 2) if v is not None else None) for k, v in components.items()}
    return {
        "final_score": final,
        "score_breakdown": {
            "price_momentum_score": rounded["price_momentum"],
            "revenue_acceleration_score": rounded["revenue_acceleration"],
            "margin_expansion_score": rounded["margin_expansion"],
            "backlog_rpo_score": rounded["backlog_rpo"],
            "customer_capex_exposure_score": rounded["customer_capex_exposure"],
            "bottleneck_evidence_score": rounded["bottleneck_evidence"],
            "valuation_risk_score": rounded["valuation_risk"],
        },
        "coverage": coverage,
        "hiddenness_level": hidden["hiddenness_level"],
        "hiddenness_score": hidden["hiddenness_score"],
        "confidence_level": conf["confidence_level"],
        "confidence_score": conf["confidence_score"],
        "insufficient_data": final is None or coverage == 0.0,
    }
