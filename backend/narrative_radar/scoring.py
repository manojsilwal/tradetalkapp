"""
Theme-lifecycle scoring (Plan §3, §7, §8) — pure, offline-testable.

Converts the raw theme features from ``features.py`` into the plan's top-level
0-100 scores and a deterministic lifecycle phase. Cross-sectional metrics
(relative strength, momentum) are percentile-ranked **across themes** so a theme
is ranked against its peers, exactly like the Picks & Shovels company scorer.

Anti-hallucination (Plan §17, §18): signal families with no data wired yet
(institutional / productization / narrative / retail / macro in the MVP) return
``None`` and lower the confidence score — they are never fabricated as 50 and
passed off as real. Phase-composite scores renormalize over present components.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from ..actionable_companies import _clamp, _linscore
from ..picks_shovels.scoring import confidence_level, percentile_rank

# Signal families and whether the MVP wires them. Confidence scales with how many
# families actually contributed data for a theme.
MARKET_FAMILY = "market_confirmation"
BREADTH_FAMILY = "breadth_quality"
DEFERRED_FAMILIES = [
    "institutional_conviction",
    "retail_saturation",
    "narrative_reality_alignment",
    "productization",
    "macro_tailwind",
]
ALL_FAMILIES = [MARKET_FAMILY, BREADTH_FAMILY] + DEFERRED_FAMILIES


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
    def build(cls, feature_rows: Sequence[Dict[str, Any]]) -> "ThemeContext":
        pops: Dict[str, List[float]] = {k: [] for k in cls.RANKED}
        for row in feature_rows:
            for key, field in cls.RANKED.items():
                v = row.get(field)
                if v is not None:
                    try:
                        pops[key].append(float(v))
                    except (TypeError, ValueError):
                        pass
        return cls(populations=pops)

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
    """
    Plan §7.6 — quality of participation, not just winners. A positive
    cap-vs-equal spread (mega-caps carrying the basket) is a *narrowing-leadership*
    red flag and lowers breadth quality.
    """
    spread = feat.get("cap_vs_equal_spread_pct")
    # More negative spread (equal-weight leading) → healthier breadth.
    breadth_inclusion = _linscore(-(spread if spread is not None else 0.0), -6.0, 6.0) if spread is not None else None
    return _blend([
        (0.30, feat.get("pct_above_50dma")),
        (0.25, feat.get("pct_above_200dma")),
        (0.25, feat.get("breadth_positive_pct")),
        (0.20, breadth_inclusion),
    ])


# ── Phase / composite scores ───────────────────────────────────────────────────


def _phase_scores(
    feat: Dict[str, Any],
    ctx: ThemeContext,
    market: Optional[float],
    breadth: Optional[float],
) -> Dict[str, Optional[float]]:
    rs_ratio_pct = ctx.rank("rs_ratio", feat.get("rs_ratio"))
    rs_mom_pct = ctx.rank("rs_momentum", feat.get("rs_momentum"))
    spread = feat.get("cap_vs_equal_spread_pct")

    # Formation: turning up from a low relative-strength base (low RS, rising momentum).
    formation = _blend([
        (0.5, (100.0 - rs_ratio_pct) if rs_ratio_pct is not None else None),
        (0.5, rs_mom_pct),
    ])

    # Acceleration: confirmed trend = market confirmation + breadth.
    acceleration = _blend([
        (0.55, market),
        (0.45, breadth),
    ])

    # Accumulation: breadth improving with moderate confirmation (institutional added later).
    accumulation = _blend([
        (0.50, breadth),
        (0.30, market),
        (0.20, formation),
    ])

    # Distribution risk: narrowing leadership + elevated RS with weakening momentum.
    narrowing = _linscore(spread, -2.0, 8.0) if spread is not None else None
    elevated_but_weak = (
        _clamp(rs_ratio_pct - rs_mom_pct + 50.0)
        if (rs_ratio_pct is not None and rs_mom_pct is not None) else None
    )
    distribution_risk = _blend([
        (0.5, narrowing),
        (0.5, elevated_but_weak),
    ])

    # Exit risk: weak/negative RS momentum + breadth deterioration.
    exit_risk = _blend([
        (0.5, (100.0 - rs_mom_pct) if rs_mom_pct is not None else None),
        (0.5, (100.0 - breadth) if breadth is not None else None),
    ])

    return {
        "theme_formation_score": formation,
        "theme_accumulation_score": accumulation,
        "theme_acceleration_score": acceleration,
        "theme_distribution_risk_score": distribution_risk,
        "theme_exit_risk_score": exit_risk,
    }


def score_theme(feat: Dict[str, Any], ctx: ThemeContext) -> Dict[str, Any]:
    """Compute all 0-100 scores + confidence for one theme's raw features."""
    market = market_confirmation_score(feat, ctx)
    breadth = breadth_quality_score(feat)
    phases = _phase_scores(feat, ctx, market, breadth)

    scores: Dict[str, Optional[float]] = {
        "market_confirmation_score": market,
        "breadth_quality_score": breadth,
        **phases,
        # Deferred families (no source wired in the MVP) — explicitly None, not fabricated.
        "institutional_conviction_score": None,
        "retail_saturation_score": None,
        "narrative_reality_alignment_score": None,
        "productization_score": None,
        "macro_tailwind_score": None,
    }

    # Confidence: data completeness on what we measure + number of families present.
    measured = [market, breadth]
    coverage = round(sum(1 for v in measured if v is not None) / len(measured), 3)
    families_present = sum(1 for v in measured if v is not None)
    conf = confidence_level(coverage, families_present)

    available = [f for f, v in (
        (MARKET_FAMILY, market), (BREADTH_FAMILY, breadth)
    ) if v is not None]
    unavailable = [f for f in DEFERRED_FAMILIES] + [
        f for f, v in ((MARKET_FAMILY, market), (BREADTH_FAMILY, breadth)) if v is None
    ]

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
