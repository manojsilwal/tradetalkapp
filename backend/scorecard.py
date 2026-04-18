"""
Risk-Return-Ratio Scorecard — deterministic math engine.

This module implements the quantitative half of the Risk-to-Return methodology
(Steps 1-3, 4, 6, 7). The subjective scores that require qualitative judgment —
Execution Risk (Step 2c rubric) and Skin-In-The-Game / SITG (Step 2e rubric) —
are produced by LLM personas in :mod:`backend.routers.scorecard` and passed in
via :class:`ScorecardInput`.

Design goals:

* **Reproducible**: normalization, weighted sums, PE-stretch guard, interpretation
  bands all live in pure Python so the same inputs always produce the same ratio.
* **Basket-aware**: the primary call is ``score_basket(rows, preset=...)`` because
  normalization ``norm(v) = v / max(set) * 10`` needs the peer set.
* **Single-ticker fallback**: ``score_single(row, medians=...)`` normalizes
  against industry medians instead, matching Step 1's note.
* **Testable**: every step returns intermediate sub-scores so unit tests can
  pin the Step 5 worked example (HUBB/PWR/ETN/GEV/NEE/MTZ) to the published
  ratios within tolerance.

References: see the methodology prose in the product spec for the full rubric;
this module intentionally keeps only the formulas.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Mapping, Optional


# ── Investor-type preset weights (Step 4 table) ──────────────────────────────
#
# Nine weights per preset:
#   w1 EPS growth   | w2 Rev growth | w3 PT upside | w4 Dividend (return)
#   w5 PE stretch   | w6 Beta       | w7 Execution | w8 D/E leverage (risk)
#   w9 Skin-in-the-game (return amplifier)
#
# These must match the spec exactly — tests assert value-by-value.

@dataclass(frozen=True)
class Weights:
    w1: float  # EPS growth (return)
    w2: float  # Revenue growth (return)
    w3: float  # PT upside (return)
    w4: float  # Dividend yield (return)
    w5: float  # PE stretch (risk)
    w6: float  # Beta (risk)
    w7: float  # Execution risk (risk)
    w8: float  # D/E leverage (risk)
    w9: float  # Skin-in-the-game (return amplifier)

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


PRESETS: Dict[str, Weights] = {
    "growth":   Weights(w1=5, w2=5, w3=3, w4=0, w5=2, w6=2, w7=4, w8=1, w9=4),
    "value":    Weights(w1=2, w2=2, w3=5, w4=2, w5=5, w6=2, w7=3, w8=3, w9=3),
    "income":   Weights(w1=1, w2=1, w3=2, w4=5, w5=3, w6=3, w7=2, w8=4, w9=2),
    "balanced": Weights(w1=3, w2=3, w3=2, w4=1, w5=3, w6=2, w7=3, w8=2, w9=4),
}


# ── Interpretation bands (Step 3) ────────────────────────────────────────────

_INTERPRETATION_BANDS = [
    (2.5, "Exceptional", "Strong buy consideration"),
    (2.0, "Strong buy",  "High conviction long"),
    (1.5, "Favorable",   "Buy on weakness"),
    (1.0, "Balanced",    "Hold, monitor catalysts"),
    (0.7, "Caution",     "Reduce or wait"),
    (0.0, "Avoid",       "Return does not justify risk"),
]


def interpret_ratio(ratio: float) -> Dict[str, str]:
    """Map a numeric ratio to its signal band (Step 3 interpretation table)."""
    for threshold, signal, action in _INTERPRETATION_BANDS:
        if ratio >= threshold:
            return {"signal": signal, "action": action}
    return {"signal": "Avoid", "action": "Return does not justify risk"}


# ── Quadrant classification (Step 6) ─────────────────────────────────────────
#
# The spec shows a 2x2 with return on the y-axis and risk on the x-axis. We
# split at the midpoint of the 0-10 scale (5.0) which is the natural decision
# boundary when sub-scores are normalized 0-10.

def classify_quadrant(return_score: float, risk_score: float, *, midpoint: float = 5.0) -> str:
    """Top-left / top-right / bottom-left / bottom-right (Step 6 scatter)."""
    high_return = return_score >= midpoint
    high_risk = risk_score >= midpoint
    if high_return and not high_risk:
        return "top-left"
    if high_return and high_risk:
        return "top-right"
    if not high_return and not high_risk:
        return "bottom-left"
    return "bottom-right"


# ── Normalization helpers ────────────────────────────────────────────────────

def normalize(value: float, denominator: float) -> float:
    """``(value / denom) * 10`` clamped to [0, 10]. Denom<=0 collapses to 0."""
    if denominator is None or denominator <= 0:
        return 0.0
    raw = (float(value) / float(denominator)) * 10.0
    if raw < 0.0:
        return 0.0
    if raw > 10.0:
        return 10.0
    return raw


def compute_pe_stretch(fwd_pe: Optional[float], hist_pe: Optional[float]) -> float:
    """
    Step 2a: ``MAX(0, (fwd_pe / hist_pe) - 1)``. Stocks at or below their
    historical PE get 0 (no penalty); only premiums are scored.

    Missing inputs (either side None or non-positive) collapse to 0 — the
    conservative choice that avoids inventing a stretch that isn't there.
    """
    if fwd_pe is None or hist_pe is None:
        return 0.0
    try:
        fp = float(fwd_pe)
        hp = float(hist_pe)
    except (TypeError, ValueError):
        return 0.0
    if hp <= 0:
        return 0.0
    stretch = (fp / hp) - 1.0
    return stretch if stretch > 0.0 else 0.0


# ── Inputs & outputs ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScorecardInput:
    """
    One row of the comparison set. Fields match the Step 0 "Data to gather
    first" table. Subjective scores (exec_risk, sitg) are 0-10 and come from
    the LLM personas; all other fields are raw market data.

    ``ceo_name`` and ``sitg_archetype`` are passed through for display only;
    the math never reads them.
    """
    ticker: str
    eps_growth_pct: float              # forward EPS growth, midpoint of guidance
    revenue_growth_pct: float          # forward revenue growth, midpoint
    pt_upside_pct: float               # analyst consensus PT / price - 1 (as %)
    dividend_yield_pct: float
    forward_pe: Optional[float]
    historical_avg_pe: Optional[float] # 5y avg forward PE
    beta: float
    exec_risk_score: float             # 1-10, from execution_risk_scorer persona
    debt_to_equity: float
    sitg_score: float                  # 0-10, from sitg_scorer persona
    ceo_name: str = ""
    sitg_archetype: str = ""


@dataclass
class ReturnScore:
    eps_score: float
    revenue_score: float
    pt_upside_score: float
    dividend_score: float
    sitg_score: float   # normalized 0-10 (passed through unchanged)
    weighted: float     # final weighted average on 0-10


@dataclass
class RiskScore:
    pe_stretch_score: float
    beta_score: float
    exec_score: float   # passed through from exec_risk_scorer
    leverage_score: float
    weighted: float


@dataclass
class ScorecardRow:
    ticker: str
    ceo_name: str
    sitg_archetype: str
    return_score: ReturnScore
    risk_score: RiskScore
    ratio: float
    sitg_boost: float   # absolute lift vs a hypothetical SITG=0 run
    signal: str
    action: str
    quadrant: str


@dataclass
class BasketResult:
    preset: str
    weights: Weights
    denominators: Dict[str, float]  # per-metric max used for normalization
    rows: List[ScorecardRow]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preset": self.preset,
            "weights": self.weights.as_dict(),
            "denominators": self.denominators,
            "rows": [_row_to_dict(r) for r in self.rows],
        }


def _row_to_dict(r: ScorecardRow) -> Dict[str, Any]:
    return {
        "ticker": r.ticker,
        "ceo_name": r.ceo_name,
        "sitg_archetype": r.sitg_archetype,
        "return_score": asdict(r.return_score),
        "risk_score": asdict(r.risk_score),
        "ratio": r.ratio,
        "sitg_boost": r.sitg_boost,
        "signal": r.signal,
        "action": r.action,
        "quadrant": r.quadrant,
    }


# ── Preset resolution ────────────────────────────────────────────────────────

def resolve_weights(
    preset: str = "balanced",
    overrides: Optional[Mapping[str, float]] = None,
) -> Weights:
    """Look up a preset by name, then apply sparse user-specified overrides."""
    key = (preset or "balanced").strip().lower()
    if key not in PRESETS:
        raise ValueError(
            f"unknown preset {preset!r}; expected one of {sorted(PRESETS.keys())}"
        )
    base = PRESETS[key]
    if not overrides:
        return base
    merged = base.as_dict()
    for k, v in overrides.items():
        if k not in merged:
            raise ValueError(f"unknown weight key {k!r}; expected one of w1..w9")
        merged[k] = float(v)
    return Weights(**merged)


# ── Core scoring ─────────────────────────────────────────────────────────────

def _denominators(rows: List[ScorecardInput]) -> Dict[str, float]:
    """
    Basket-wide normalization denominators. Per Step 1, each metric's score is
    ``(value / max_in_set) * 10``. For PE stretch we compute per-ticker first
    then take the max stretch.
    """
    if not rows:
        raise ValueError("score_basket requires at least one ScorecardInput row")
    pe_stretches = [compute_pe_stretch(r.forward_pe, r.historical_avg_pe) for r in rows]
    # Use 0 as the floor so an all-zero column doesn't raise in normalize().
    return {
        "eps_growth_pct": max((r.eps_growth_pct for r in rows), default=0.0),
        "revenue_growth_pct": max((r.revenue_growth_pct for r in rows), default=0.0),
        "pt_upside_pct": max((r.pt_upside_pct for r in rows), default=0.0),
        "dividend_yield_pct": max((r.dividend_yield_pct for r in rows), default=0.0),
        "pe_stretch": max(pe_stretches, default=0.0),
        "beta": max((r.beta for r in rows), default=0.0),
        "debt_to_equity": max((r.debt_to_equity for r in rows), default=0.0),
    }


def _score_one(
    row: ScorecardInput,
    denom: Mapping[str, float],
    weights: Weights,
    *,
    include_sitg: bool = True,
) -> tuple[ReturnScore, RiskScore, float]:
    """Return (ReturnScore, RiskScore, ratio) using the provided denominators."""
    eps = normalize(row.eps_growth_pct, denom["eps_growth_pct"])
    rev = normalize(row.revenue_growth_pct, denom["revenue_growth_pct"])
    pt = normalize(row.pt_upside_pct, denom["pt_upside_pct"])
    div = normalize(row.dividend_yield_pct, denom["dividend_yield_pct"])
    # SITG is scored 0-10 directly by the LLM persona — no further normalization.
    sitg = max(0.0, min(10.0, float(row.sitg_score))) if include_sitg else 0.0

    stretch = compute_pe_stretch(row.forward_pe, row.historical_avg_pe)
    pe_stretch_score = normalize(stretch, denom["pe_stretch"])
    beta_score = normalize(row.beta, denom["beta"])
    # Exec risk is already 0-10 from the LLM persona — no further normalization.
    exec_score = max(0.0, min(10.0, float(row.exec_risk_score)))
    lev_score = normalize(row.debt_to_equity, denom["debt_to_equity"])

    return_num = (
        eps * weights.w1
        + rev * weights.w2
        + pt * weights.w3
        + div * weights.w4
        + sitg * weights.w9
    )
    return_den = weights.w1 + weights.w2 + weights.w3 + weights.w4 + weights.w9
    return_weighted = return_num / return_den if return_den > 0 else 0.0

    risk_num = (
        pe_stretch_score * weights.w5
        + beta_score * weights.w6
        + exec_score * weights.w7
        + lev_score * weights.w8
    )
    risk_den = weights.w5 + weights.w6 + weights.w7 + weights.w8
    risk_weighted = risk_num / risk_den if risk_den > 0 else 0.0

    # Step 3: cap the ratio at return_weighted * 5 when risk is near zero, to
    # avoid division artifacts for very low-risk assets.
    if risk_weighted < 0.2:
        ratio = min(return_weighted * 5.0, return_weighted / 0.2)
    else:
        ratio = return_weighted / risk_weighted

    return (
        ReturnScore(
            eps_score=round(eps, 4),
            revenue_score=round(rev, 4),
            pt_upside_score=round(pt, 4),
            dividend_score=round(div, 4),
            sitg_score=round(sitg, 4),
            weighted=round(return_weighted, 4),
        ),
        RiskScore(
            pe_stretch_score=round(pe_stretch_score, 4),
            beta_score=round(beta_score, 4),
            exec_score=round(exec_score, 4),
            leverage_score=round(lev_score, 4),
            weighted=round(risk_weighted, 4),
        ),
        round(ratio, 4),
    )


def score_basket(
    rows: List[ScorecardInput],
    preset: str = "balanced",
    *,
    weights_override: Optional[Mapping[str, float]] = None,
    situational_flags: Optional[Mapping[str, bool]] = None,
) -> BasketResult:
    """
    Score a basket of tickers against each other. Primary entrypoint.

    ``situational_flags`` applies the Step 7 adjustments before scoring. See
    :func:`apply_situational_adjustments` for the supported flag names.
    """
    if not rows:
        raise ValueError("score_basket requires at least one row")
    weights = resolve_weights(preset, weights_override)
    weights = apply_situational_adjustments(weights, situational_flags or {})
    denom = _denominators(rows)

    result_rows: List[ScorecardRow] = []
    for row in rows:
        ret, risk, ratio = _score_one(row, denom, weights, include_sitg=True)
        _ret_no_sitg, _risk_no_sitg, ratio_no_sitg = _score_one(
            row, denom, weights, include_sitg=False
        )
        boost = round(ratio - ratio_no_sitg, 4)
        interp = interpret_ratio(ratio)
        quadrant = classify_quadrant(ret.weighted, risk.weighted)
        result_rows.append(
            ScorecardRow(
                ticker=row.ticker,
                ceo_name=row.ceo_name,
                sitg_archetype=row.sitg_archetype,
                return_score=ret,
                risk_score=risk,
                ratio=ratio,
                sitg_boost=boost,
                signal=interp["signal"],
                action=interp["action"],
                quadrant=quadrant,
            )
        )
    return BasketResult(
        preset=(preset or "balanced").lower(),
        weights=weights,
        denominators={k: round(float(v), 4) for k, v in denom.items()},
        rows=result_rows,
    )


def score_single(
    row: ScorecardInput,
    preset: str = "balanced",
    *,
    industry_medians: Optional[Mapping[str, float]] = None,
    weights_override: Optional[Mapping[str, float]] = None,
    situational_flags: Optional[Mapping[str, bool]] = None,
) -> ScorecardRow:
    """
    Score a single ticker against industry medians (Step 1 fallback). When
    ``industry_medians`` is not provided, a neutral "self" denominator is used
    so the ticker lands mid-scale — useful for UI previews but not for buy/sell.
    """
    medians = dict(industry_medians or {})
    denom = {
        "eps_growth_pct": medians.get("eps_growth_pct", max(row.eps_growth_pct, 1.0)),
        "revenue_growth_pct": medians.get(
            "revenue_growth_pct", max(row.revenue_growth_pct, 1.0)
        ),
        "pt_upside_pct": medians.get("pt_upside_pct", max(row.pt_upside_pct, 1.0)),
        "dividend_yield_pct": medians.get(
            "dividend_yield_pct", max(row.dividend_yield_pct, 0.1)
        ),
        "pe_stretch": medians.get(
            "pe_stretch",
            max(compute_pe_stretch(row.forward_pe, row.historical_avg_pe), 0.1),
        ),
        "beta": medians.get("beta", max(row.beta, 1.0)),
        "debt_to_equity": medians.get("debt_to_equity", max(row.debt_to_equity, 0.5)),
    }
    weights = resolve_weights(preset, weights_override)
    weights = apply_situational_adjustments(weights, situational_flags or {})
    ret, risk, ratio = _score_one(row, denom, weights, include_sitg=True)
    _ret0, _risk0, ratio_no_sitg = _score_one(row, denom, weights, include_sitg=False)
    interp = interpret_ratio(ratio)
    quadrant = classify_quadrant(ret.weighted, risk.weighted)
    return ScorecardRow(
        ticker=row.ticker,
        ceo_name=row.ceo_name,
        sitg_archetype=row.sitg_archetype,
        return_score=ret,
        risk_score=risk,
        ratio=ratio,
        sitg_boost=round(ratio - ratio_no_sitg, 4),
        signal=interp["signal"],
        action=interp["action"],
        quadrant=quadrant,
    )


# ── Step 7 situational adjustments ───────────────────────────────────────────
#
# These are weight multipliers the user (or the LLM) can toggle. Keeping them
# in code instead of the prompt guarantees reproducibility: the same flag with
# the same weights always produces the same adjustment.

_SITUATIONAL_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    # Utilities carry structural debt; avoid double-penalizing D/E.
    "utilities_vs_industrials": {"w8": 0.5},
    # Bear market / rising rates — beta matters more.
    "bear_or_rate_hike": {"w6": 2.0},
    # M&A integration year — execution risk is under-appreciated.
    "ma_integration": {"w7": 1.5},
    # Missed last 2 earnings — execution risk multiplier 1.5x for current quarter.
    "missed_2_earnings": {"w7": 1.5},
    # CEO recently sold >20% of holdings — absorb into SITG by lowering w9
    # (we can't lower the per-ticker score from here; dampen the whole weight).
    "ceo_sold_20pct_plus": {"w9": 0.5},
    # Recent IPO — SITG unreliable, discount w9 by 50%.
    "recent_ipo_lt_2y": {"w9": 0.5},
    # CEO compensation is 90%+ cash — dampen SITG weight.
    "ceo_comp_mostly_cash": {"w9": 0.6},
}


def apply_situational_adjustments(
    weights: Weights,
    flags: Mapping[str, bool],
) -> Weights:
    """
    Step 7: multiply weights by situational factors. Unknown flags are ignored
    (the LLM may emit new flag names; we stay permissive and log via return
    value inspection in tests).
    """
    if not flags:
        return weights
    merged = weights.as_dict()
    for flag_name, is_on in flags.items():
        if not is_on:
            continue
        mult = _SITUATIONAL_MULTIPLIERS.get(flag_name)
        if not mult:
            continue
        for wkey, factor in mult.items():
            merged[wkey] = merged[wkey] * factor
    return Weights(**merged)


__all__ = [
    "Weights",
    "PRESETS",
    "ScorecardInput",
    "ReturnScore",
    "RiskScore",
    "ScorecardRow",
    "BasketResult",
    "normalize",
    "compute_pe_stretch",
    "interpret_ratio",
    "classify_quadrant",
    "resolve_weights",
    "score_basket",
    "score_single",
    "apply_situational_adjustments",
]
