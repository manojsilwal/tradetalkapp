"""
Theme-lifecycle scoring (Plan §3, §7, §8) — pure, offline-testable.

Converts raw theme features (``features.py``) + optional signal families
(``signals.py``: institutional 13F, ETF productization/flows, narrative/media,
retail saturation, fundamentals reality, macro) into the plan's top-level 0-100
scores and a deterministic lifecycle phase.

Cross-sectional metrics (relative strength, momentum) are percentile-ranked across
themes, like the Picks & Shovels company scorer. Signal families that are not
available (no source wired / data missing) return ``None`` — never fabricated —
and lower the confidence score. Phase composites renormalize over present
components, so the MVP (market + breadth only) keeps working and richer scans
simply incorporate more families.

``score_theme(feat, ctx, signals=None)`` is backward compatible: with no signals,
only market-confirmation + breadth are scored (the NR-1..NR-4 MVP behavior).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from ..actionable_companies import _clamp, _linscore
from ..picks_shovels.scoring import confidence_level, percentile_rank
from . import smart_money as nr_smart_money
from . import themes as nr_themes

# The eight signal families confidence is measured against (Plan §3).
FAMILIES = [
    "market_confirmation",
    "breadth_quality",
    "institutional_conviction",
    "productization",
    "narrative",
    "retail_saturation",
    "narrative_reality_alignment",
    "macro_tailwind",
]
# Families wired in the MVP (NR-1..NR-4); the rest arrive via ``signals``.
MVP_FAMILIES = ["market_confirmation", "breadth_quality"]
DEFERRED_FAMILIES = [f for f in FAMILIES if f not in MVP_FAMILIES]


@dataclass
class ThemeContext:
    """Cross-sectional percentile populations across all scanned themes."""

    populations: Dict[str, List[float]]

    RANKED = {
        "rs_ratio": "rs_ratio",
        "rs_momentum": "rs_momentum",
        "median_ret_3m": "median_ret_3m_pct",
        "median_ret_6m": "median_ret_6m_pct",
        "median_ret_12m": "median_ret_12m_pct",
    }

    @classmethod
    def build(cls, feature_rows: Sequence[Dict[str, Any]], *, group: Optional[str] = None) -> "ThemeContext":
        rows = feature_rows
        if group is not None:
            rows = [r for r in feature_rows if nr_themes.theme_group(r.get("theme_id", "")) == group]
        pops: Dict[str, List[float]] = {k: [] for k in cls.RANKED}
        for row in rows:
            for key, field in cls.RANKED.items():
                v = row.get(field)
                if v is not None:
                    try:
                        pops[key].append(float(v))
                    except (TypeError, ValueError):
                        pass
        return cls(populations=pops)

    @classmethod
    def build_by_group(cls, feature_rows: Sequence[Dict[str, Any]]) -> Dict[str, "ThemeContext"]:
        groups_seen: Dict[str, "ThemeContext"] = {}
        for g in nr_themes.groups():
            groups_seen[g] = cls.build(feature_rows, group=g)
        return groups_seen

    def rank(self, metric: str, value: Optional[float]) -> Optional[float]:
        return percentile_rank(value, self.populations.get(metric, []))


def _blend(parts: List[tuple]) -> Optional[float]:
    """Weighted blend of ``[(weight, value), ...]`` renormalizing over present values."""
    acc = 0.0
    used = 0.0
    for w, v in parts:
        if v is None:
            continue
        acc += w * v
        used += w
    if used == 0:
        return None
    return round(_clamp(acc / used), 2)


# ── Family scores (0-100) ─────────────────────────────────────────────────────


def market_confirmation_score(feat: Dict[str, Any], ctx: ThemeContext) -> Optional[float]:
    """Plan §7.5 — RS vs SPY + RS momentum + price-momentum percentiles."""
    return _blend([
        (0.25, ctx.rank("rs_ratio", feat.get("rs_ratio"))),
        (0.25, ctx.rank("rs_momentum", feat.get("rs_momentum"))),
        (0.20, ctx.rank("median_ret_3m", feat.get("median_ret_3m_pct"))),
        (0.15, ctx.rank("median_ret_6m", feat.get("median_ret_6m_pct"))),
        (0.15, ctx.rank("median_ret_12m", feat.get("median_ret_12m_pct"))),
    ])


def breadth_quality_score(feat: Dict[str, Any]) -> Optional[float]:
    """Plan §7.6 — quality of participation. Positive cap-vs-equal spread (mega-caps
    carrying the basket) is a narrowing-leadership red flag and lowers breadth."""
    spread = feat.get("cap_vs_equal_spread_pct")
    breadth_inclusion = _linscore(-(spread if spread is not None else 0.0), -6.0, 6.0) if spread is not None else None
    return _blend([
        (0.30, feat.get("pct_above_50dma")),
        (0.25, feat.get("pct_above_200dma")),
        (0.25, feat.get("breadth_positive_pct")),
        (0.20, breadth_inclusion),
    ])


def institutional_conviction_score(
    inst: Optional[Dict[str, Any]],
    fast_proxy: Optional[float],
    etf_flow: Optional[Dict[str, Any]],
    smart_money: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """
    Plan §7.4 — weeks-fresh smart-money leg + optional 13F/ETF-flow confirmation.
    When ``smart_money`` is available it replaces the market+breadth fast proxy.
    Returns None unless at least one real institutional proxy exists.
    """
    has_13f = bool(inst and inst.get("available"))
    has_flow = bool(etf_flow and etf_flow.get("available"))
    has_sm = bool(smart_money and smart_money.get("available"))
    if not has_13f and not has_flow and not has_sm:
        return None

    slow = None
    if has_13f:
        conc = inst.get("concentration_pct")
        slow = _blend([
            (0.35, inst.get("ownership_breadth_pct")),
            (0.25, _linscore(inst.get("net_position_change_pct"), -20.0, 30.0)),
            (0.20, _clamp((inst.get("new_position_ratio") or 0.0) * 100.0)),
            (0.20, (100.0 - conc) if conc is not None else None),
        ])

    flow_score = etf_flow.get("flow_score") if has_flow else None
    sm_score = smart_money.get("accumulation_score") if has_sm else None
    fast = _blend([
        (0.70, sm_score if has_sm else fast_proxy),
        (0.30, flow_score),
    ])
    if has_sm and not has_13f and not has_flow:
        return fast
    return _blend([(0.65, fast), (0.35, slow)])


def retail_narrative_direction_score(retail: Optional[Dict[str, Any]]) -> Optional[float]:
    """0-100 from retail_direction ∈ [-1 sell-framing, +1 buy-pump]."""
    if not retail or not retail.get("available"):
        return None
    direction = retail.get("retail_direction")
    if direction is None:
        return None
    return round(_clamp(50.0 + float(direction) * 50.0), 2)


def productization_score(prod: Optional[Dict[str, Any]], etf_flow: Optional[Dict[str, Any]]) -> Optional[float]:
    """Plan §7.3 — ETF filings/launches/AUM-growth/flows. Late launch after a big
    run-up raises saturation and lowers product quality."""
    if not prod or not prod.get("available"):
        return None
    flow_score = etf_flow.get("flow_score") if (etf_flow and etf_flow.get("available")) else None
    return _blend([
        (0.30, _linscore(prod.get("filings_count"), 0.0, 5.0)),
        (0.20, _linscore(prod.get("issuer_count"), 0.0, 4.0)),
        (0.20, _linscore(prod.get("aum_growth_pct"), -10.0, 50.0)),
        (0.20, flow_score),
        (0.10, 30.0 if prod.get("launch_after_runup") else 70.0),
    ])


def narrative_strength_score(narr: Optional[Dict[str, Any]]) -> Optional[float]:
    """Plan §7.2 — media/narrative velocity + attention percentile + sentiment."""
    if not narr or not narr.get("available"):
        return None
    return _blend([
        (0.40, narr.get("mention_velocity_pct")),
        (0.30, narr.get("attention_percentile")),
        (0.30, _linscore(narr.get("sentiment"), -1.0, 1.0)),
    ])


def retail_saturation_score(retail: Optional[Dict[str, Any]]) -> Optional[float]:
    """Plan §7.7 — social/influencer velocity + media frequency + 'buy now' density."""
    if not retail or not retail.get("available"):
        return None
    return _blend([
        (0.30, retail.get("social_velocity_pct")),
        (0.25, retail.get("media_freq_pct")),
        (0.20, retail.get("youtube_score")),
        (0.25, _linscore(retail.get("buy_now_density"), 0.0, 10.0)),
    ])


def narrative_reality_alignment_score(reality: Optional[Dict[str, Any]]) -> Optional[float]:
    """Plan §7.8 — do fundamentals support the story? Revenue accel, capex, guidance,
    filing keyword growth, estimate revisions."""
    if not reality or not reality.get("available"):
        return None
    return _blend([
        (0.30, _linscore(reality.get("revenue_accel_pct"), -10.0, 20.0)),
        (0.20, _linscore(reality.get("capex_growth_pct"), -10.0, 40.0)),
        (0.20, _linscore(reality.get("guidance_revision"), -2.0, 2.0)),
        (0.15, _linscore(reality.get("keyword_growth_pct"), -20.0, 60.0)),
        (0.15, _linscore(reality.get("estimate_revision_pct"), -10.0, 15.0)),
    ])


def macro_tailwind_score(macro: Optional[Dict[str, Any]]) -> Optional[float]:
    """Plan §5.8 — fit between the theme and the current macro regime (0-100)."""
    if not macro or not macro.get("available"):
        return None
    return _blend([(1.0, macro.get("regime_fit_pct"))])


# ── Phase / composite scores ───────────────────────────────────────────────────


def _phase_scores(
    feat: Dict[str, Any],
    ctx: ThemeContext,
    fam: Dict[str, Optional[float]],
    etf_flow: Optional[Dict[str, Any]],
    divergence: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    market = fam["market_confirmation"]
    breadth = fam["breadth_quality"]
    institutional = fam["institutional_conviction"]
    productization = fam["productization"]
    narrative = fam["narrative"]
    retail = fam["retail_saturation"]
    reality = fam["narrative_reality_alignment"]

    rs_ratio_pct = ctx.rank("rs_ratio", feat.get("rs_ratio"))
    rs_mom_pct = ctx.rank("rs_momentum", feat.get("rs_momentum"))
    spread = feat.get("cap_vs_equal_spread_pct")
    flow_score = etf_flow.get("flow_score") if (etf_flow and etf_flow.get("available")) else None
    flow_accel = etf_flow.get("flow_acceleration_pct") if (etf_flow and etf_flow.get("available")) else None

    # Formation: turning up from a low RS base; early narrative/productization; low retail.
    formation = _blend([
        (0.35, (100.0 - rs_ratio_pct) if rs_ratio_pct is not None else None),
        (0.30, rs_mom_pct),
        (0.15, narrative),
        (0.10, productization),
        (0.10, (100.0 - retail) if retail is not None else None),
    ])

    # Acceleration: confirmed trend = market + breadth + flow + narrative + reality.
    acceleration = _blend([
        (0.40, market),
        (0.25, breadth),
        (0.15, flow_score),
        (0.10, narrative),
        (0.10, reality),
    ])

    # Accumulation: breadth + institutional + divergence + reality.
    accumulation = _blend([
        (0.30, breadth),
        (0.25, institutional),
        (0.15, divergence),
        (0.15, reality),
        (0.15, market),
    ])

    # Distribution risk: retail saturation + narrowing leadership + elevated-but-weak RS
    # + narrative-reality divergence + ETF flow slowdown.
    narrowing = _linscore(spread, -2.0, 8.0) if spread is not None else None
    elevated_but_weak = (
        _clamp(rs_ratio_pct - rs_mom_pct + 50.0)
        if (rs_ratio_pct is not None and rs_mom_pct is not None) else None
    )
    nr_divergence = (
        _clamp((narrative - reality) + 50.0)
        if (narrative is not None and reality is not None) else None
    )
    flow_slowdown = (100.0 - flow_accel) if flow_accel is not None else None
    distribution_risk = _blend([
        (0.22, retail),
        (0.18, narrowing),
        (0.18, elevated_but_weak),
        (0.12, nr_divergence),
        (0.10, flow_slowdown),
        (0.10, (100.0 - (institutional or 50.0)) if institutional is not None else None),
        (0.10, (100.0 - divergence) if divergence is not None else None),
    ])

    # Exit risk: weak/negative RS momentum + breadth deterioration + flow reversal + retail.
    flow_reversal = (100.0 - flow_score) if flow_score is not None else None
    exit_risk = _blend([
        (0.35, (100.0 - rs_mom_pct) if rs_mom_pct is not None else None),
        (0.25, (100.0 - breadth) if breadth is not None else None),
        (0.20, flow_reversal),
        (0.20, retail),
    ])

    return {
        "theme_formation_score": formation,
        "theme_accumulation_score": accumulation,
        "theme_acceleration_score": acceleration,
        "theme_distribution_risk_score": distribution_risk,
        "theme_exit_risk_score": exit_risk,
    }


def score_theme(
    feat: Dict[str, Any],
    ctx: ThemeContext,
    signals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute all 0-100 scores + confidence for one theme. ``signals`` (optional)
    carries the NR-5..NR-9 family inputs; absent → MVP market+breadth behavior."""
    signals = signals or {}
    etf_flow = signals.get("etf_flow")

    market = market_confirmation_score(feat, ctx)
    breadth = breadth_quality_score(feat)
    fast_proxy = _blend([(0.5, market), (0.5, breadth)])

    fam: Dict[str, Optional[float]] = {
        "market_confirmation": market,
        "breadth_quality": breadth,
        "institutional_conviction": institutional_conviction_score(
            signals.get("institutional"), fast_proxy, etf_flow, signals.get("smart_money")
        ),
        "productization": productization_score(signals.get("productization"), etf_flow),
        "narrative": narrative_strength_score(signals.get("narrative")),
        "retail_saturation": retail_saturation_score(signals.get("retail")),
        "narrative_reality_alignment": narrative_reality_alignment_score(signals.get("reality")),
        "macro_tailwind": macro_tailwind_score(signals.get("macro")),
    }

    retail_dir = retail_narrative_direction_score(signals.get("retail"))
    sm = signals.get("smart_money") or {}
    accumulation_score = sm.get("accumulation_score") if sm.get("available") else None
    divergence = nr_smart_money.smart_money_divergence_score(
        accumulation_score,
        (signals.get("retail") or {}).get("retail_direction"),
        fam.get("retail_saturation"),
    )

    phases = _phase_scores(feat, ctx, fam, etf_flow, divergence=divergence)

    scores: Dict[str, Optional[float]] = {
        "market_confirmation_score": fam["market_confirmation"],
        "breadth_quality_score": fam["breadth_quality"],
        "institutional_conviction_score": fam["institutional_conviction"],
        "productization_score": fam["productization"],
        "narrative_score": fam["narrative"],
        "retail_saturation_score": fam["retail_saturation"],
        "retail_narrative_direction_score": retail_dir,
        "smart_money_divergence_score": divergence,
        "narrative_reality_alignment_score": fam["narrative_reality_alignment"],
        "macro_tailwind_score": fam["macro_tailwind"],
        **phases,
    }

    present = {f for f in FAMILIES if fam.get(f) is not None}
    coverage = round(len(present) / len(FAMILIES), 3)
    conf = confidence_level(coverage, len(present))

    available = [f for f in FAMILIES if f in present]
    unavailable = [f for f in FAMILIES if f not in present]

    scores = {k: (round(v, 2) if v is not None else None) for k, v in scores.items()}
    return {
        "scores": scores,
        "coverage": coverage,
        "confidence_score": conf["confidence_score"],
        "confidence_level": conf["confidence_level"],
        "available_families": available,
        "unavailable_families": unavailable,
        "insufficient_data": market is None and breadth is None,
    }
