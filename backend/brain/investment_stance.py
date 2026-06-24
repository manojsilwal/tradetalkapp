"""Investment surface — long-horizon framing on top of the finance brain.

This is an *additive presentation + scoring* layer. It consumes the existing
brain serving contract (``serve_ticker`` / Reflex output) and re-frames it for a
1-5 year investment horizon:

  * ``investment_score`` — the SAME 0-100 transparency group scores re-weighted
    with :data:`rule_baseline.LONG_HORIZON_COMPOSITE_WEIGHTS` (valuation/quality
    dominate; momentum/sentiment capped at a small "pricing context" weight). It
    does **not** retrain or mutate the model — it re-weights already-computed
    numbers, so the existing quarterly surfaces are untouched.
  * ``stance`` — investment-language vocabulary (no trading verbs).
  * ``valuation_freshness`` — reuses the Reflex invalidation result verbatim.
  * ``pricing_context`` — interprets price moves as margin-of-safety changes.

Design invariants (carried over from ``reflex.py`` and the project critique):
  - A pure price move (even a large drop) NEVER caps the stance. A lower price on
    unchanged fundamentals *improves* margin of safety — that is the long-term
    entry case, not a reason to retreat to Hold.
  - Only an anchor-breaking event (earnings/guidance/management/accounting/rate
    move) forces ``Pending Valuation Refresh``.
  - Freshness lowers CONFIDENCE, never the score.
  - No fabricated numbers: if no group score is available the surface returns
    ``Insufficient Evidence`` instead of inventing an investment score.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import (
    DISCLAIMER,
    INVESTMENT_HORIZON_DAYS,
    MIN_INVESTMENT_HORIZON_MONTHS,
)
from . import adapters
from . import rule_baseline


# Prompt/memo guardrail injected into every investment-surface narrative.
INVESTMENT_GUARDRAIL = (
    "This is an investment-research surface with a minimum 12-month horizon. "
    "Do not produce intraday trading signals, stop losses, take-profit targets, "
    "or short-term trade entries. Use short-term price movement only to discuss "
    "valuation freshness, margin-of-safety changes, and risk context."
)

# Investment-stance vocabulary (most → least constructive). Trading verbs
# (Enter Now / Scalp / Stop Loss / Take Profit / Intraday) are intentionally absent.
STANCE_ORDER = [
    "Strong Long-Term Buy",
    "Buy / Accumulate",
    "Accumulate Slowly",
    "Hold / Watch",
    "Watchlist Only",
    "Speculative / High Risk",
    "Avoid",
]
# Out-of-band stances that short-circuit the score bands.
STANCE_REFRESH = "Pending Valuation Refresh"
STANCE_INSUFFICIENT = "Insufficient Evidence"

ALLOWED_STANCES = frozenset(STANCE_ORDER + [STANCE_REFRESH, STANCE_INSUFFICIENT])

# Score → stance bands (on the 0-100 long-horizon investment score).
_SCORE_BANDS = [
    (72.0, "Strong Long-Term Buy"),
    (60.0, "Buy / Accumulate"),
    (54.0, "Accumulate Slowly"),
    (46.0, "Hold / Watch"),
    (38.0, "Watchlist Only"),
]
_LOWEST_STANCE = "Avoid"

# Confidence below this caps the upside at Hold / Watch (low-conviction guard).
_LOW_CONFIDENCE = 0.35
# |price move| beyond this is treated as a margin-of-safety change.
_MOS_MOVE_THRESHOLD = 0.05
# |price move| beyond this asks for a live repricing of the valuation gap.
_REPRICE_MOVE_THRESHOLD = 0.10


def investment_horizon_meta() -> Dict[str, Any]:
    """Canonical horizon metadata stamped on every investment-surface response."""
    return {
        "analysis_type": "investment_research",
        "minimum_horizon_months": MIN_INVESTMENT_HORIZON_MONTHS,
        "primary_horizon": "1_to_3_years",
        "long_term_horizon": "3_to_5_years",
        "horizon_days": dict(INVESTMENT_HORIZON_DAYS),
    }


def _cap(stance: str, max_stance: str) -> str:
    """Return the less-constructive of two ordered stances."""
    if stance not in STANCE_ORDER or max_stance not in STANCE_ORDER:
        return stance
    return stance if STANCE_ORDER.index(stance) >= STANCE_ORDER.index(max_stance) else max_stance


def _band_stance(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    for threshold, stance in _SCORE_BANDS:
        if score >= threshold:
            return stance
    return _LOWEST_STANCE


def _classify_reasons(reasons: List[str]) -> Dict[str, bool]:
    """Split Reflex invalidation reasons into event vs pure-age."""
    event = any(
        r.startswith("material_event") or r.startswith("discount_rate_moved")
        for r in (reasons or [])
    )
    age_only = bool(reasons) and all(r.startswith("stale_age") for r in reasons)
    return {"event": event, "age_only": age_only}


def _valuation_freshness(brain_result: Dict, move: Optional[float]) -> Dict[str, Any]:
    """Reuse the Reflex invalidation result as the Valuation Freshness Monitor.

    NB: a pure price move never forces a full refresh — it only asks for a live
    repricing of the valuation gap. Only an anchor-breaking event does.
    """
    status = brain_result.get("status")
    reasons = brain_result.get("reasons") or []
    cls = _classify_reasons(reasons)
    move_pct = round(move * 100.0, 2) if move is not None else None

    if status == "INVALID_INPUT":
        return {
            "status": "invalid_input",
            "requires_full_valuation_refresh": True,
            "requires_event_review": False,
            "price_change_since_valuation_pct": move_pct,
            "reason": "Live price input failed sanity checks; valuation cannot be repriced.",
        }
    if status == "INVALID" or cls["event"]:
        return {
            "status": "needs_full_valuation_refresh",
            "requires_full_valuation_refresh": True,
            "requires_event_review": True,
            "price_change_since_valuation_pct": move_pct,
            "reason": "Anchor-breaking event since last valuation ("
                      + ", ".join(reasons) + "). Refresh the full valuation before a constructive stance.",
        }
    if status == "STALE" or cls["age_only"]:
        return {
            "status": "stale_recompute_queued",
            "requires_full_valuation_refresh": False,
            "requires_event_review": False,
            "price_change_since_valuation_pct": move_pct,
            "reason": "Snapshot age exceeded the freshness window; a recompute is queued. "
                      "The long-term thesis is unchanged in the meantime.",
        }
    # LIVE (or base-only) — decide between fresh vs needs-live-repricing on the
    # magnitude of the price move (margin-of-safety read), never a stance cap.
    if move is not None and abs(move) >= _REPRICE_MOVE_THRESHOLD:
        return {
            "status": "needs_live_repricing",
            "requires_full_valuation_refresh": False,
            "requires_event_review": False,
            "price_change_since_valuation_pct": move_pct,
            "reason": "Price moved materially since the last valuation. Re-read the "
                      "margin of safety before finalizing the stance.",
        }
    return {
        "status": "fresh",
        "requires_full_valuation_refresh": False,
        "requires_event_review": False,
        "price_change_since_valuation_pct": move_pct,
        "reason": "Valuation is current relative to price and recent events.",
    }


def _pricing_context(valuation: Dict, move: Optional[float]) -> Dict[str, Any]:
    """Interpret the price move as a margin-of-safety change (not a trade signal)."""
    dcf_upside = valuation.get("dcf_upside_live")
    if dcf_upside is None:
        dcf_upside = valuation.get("dcf_upside_at_base")
    move_pct = round(move * 100.0, 2) if move is not None else None
    changed = move is not None and abs(move) >= _MOS_MOVE_THRESHOLD

    if move is None:
        note = "No live price move available relative to the last valuation."
    elif move <= -_MOS_MOVE_THRESHOLD:
        note = (f"Price fell {abs(move_pct)}% since the last valuation, improving the "
                "margin of safety on an unchanged thesis (long-term entry context, "
                "not a trade signal).")
    elif move >= _MOS_MOVE_THRESHOLD:
        note = (f"Price rose {move_pct}% since the last valuation, reducing the margin "
                "of safety.")
    else:
        note = "Price is roughly unchanged since the last valuation; margin of safety is intact."
    return {
        "price_move_since_valuation_pct": move_pct,
        "margin_of_safety_changed": bool(changed),
        "current_dcf_upside": dcf_upside,
        "pricing_note": note,
    }


def _resolve_move(brain_result: Dict, valuation: Dict) -> Optional[float]:
    """Best-effort price move since the base valuation (as a fraction)."""
    fresh = brain_result.get("freshness") or {}
    if fresh.get("move_since_base") is not None:
        return float(fresh["move_since_base"])
    base = valuation.get("base_price")
    live = valuation.get("live_price")
    if base and live and base != 0:
        return float(live) / float(base) - 1.0
    return None


def _decide_stance(
    score: Optional[float],
    *,
    confidence: Optional[float],
    freshness_status: str,
    business_type: Optional[str],
    risk_group: Optional[float],
) -> Dict[str, Any]:
    """Map score + context to an investment stance. Price moves never cap it."""
    # Anchor-breaking refresh and missing-data short-circuits come first.
    if freshness_status == "needs_full_valuation_refresh":
        return {"stance": STANCE_REFRESH, "max_allowed_stance": STANCE_REFRESH,
                "stance_reason": "Material event invalidated the last valuation; refresh required before a Buy."}
    if score is None or freshness_status == "invalid_input":
        return {"stance": STANCE_INSUFFICIENT, "max_allowed_stance": STANCE_INSUFFICIENT,
                "stance_reason": "Insufficient evidence to score the business over a 12-month+ horizon."}

    stance = _band_stance(score)
    caps: List[str] = []

    # Low-conviction guard (confidence, NOT price move).
    if confidence is not None and confidence < _LOW_CONFIDENCE:
        stance = _cap(stance, "Hold / Watch")
        caps.append("low_confidence")

    # High-growth unprofitable + weak balance-sheet/risk → speculative label.
    if business_type == "high_growth_unprofitable" and risk_group is not None and risk_group < 45.0:
        if stance in ("Strong Long-Term Buy", "Buy / Accumulate", "Accumulate Slowly"):
            stance = "Speculative / High Risk"
            caps.append("high_growth_weak_balance_sheet")

    return {
        "stance": stance,
        "max_allowed_stance": stance,
        "stance_reason": "Driven by valuation + business quality over a 1-5 year horizon"
                         + (f"; capped ({', '.join(caps)})" if caps else "."),
    }


def _investment_memo(
    ticker: str,
    stance: str,
    valuation: Dict,
    pricing_context: Dict,
    valuation_freshness: Dict,
    business_type: Optional[str],
    investment_score: Optional[float],
    confidence: Optional[float],
) -> Dict[str, Any]:
    iv_mid = valuation.get("intrinsic_value_mid")
    status = valuation.get("status")
    return {
        "investment_horizon": "1-3 years primary; 3-5 years long-term (minimum 12 months)",
        "business_valuation_view": (
            f"{ticker} business type {business_type or 'unclassified'}; intrinsic value (mid) "
            f"{iv_mid if iv_mid is not None else 'n/a'}, valuation status {status or 'n/a'}."
        ),
        "pricing_context_view": pricing_context.get("pricing_note"),
        "recent_event_freshness_view": valuation_freshness.get("reason"),
        "risk_committee_view": (
            f"Investment score {investment_score if investment_score is not None else 'n/a'}/100, "
            f"confidence {round(confidence, 2) if confidence is not None else 'n/a'}. Freshness lowers "
            "confidence, never the score; price moves are read as margin-of-safety changes."
        ),
        "final_long_term_stance": stance,
        "guardrail": INVESTMENT_GUARDRAIL,
        "disclaimer": DISCLAIMER,
    }


def build_investment_analysis(brain_result: Dict) -> Dict[str, Any]:
    """Wrap a brain serving contract into the long-horizon investment surface."""
    block = adapters._block(brain_result)
    group_scores = block.get("signal_scores") or {}
    confidence = brain_result.get("confidence_score")
    valuation = dict(brain_result.get("valuation") or {})
    business = brain_result.get("business") or {}
    business_type = business.get("business_type") or valuation.get("business_type")
    risk_group = group_scores.get("risk")

    investment_score = rule_baseline.composite_from_group_scores(
        group_scores, rule_baseline.LONG_HORIZON_COMPOSITE_WEIGHTS
    )

    move = _resolve_move(brain_result, valuation)
    valuation_freshness = _valuation_freshness(brain_result, move)
    pricing_context = _pricing_context(valuation, move)

    decision = _decide_stance(
        investment_score,
        confidence=confidence,
        freshness_status=valuation_freshness["status"],
        business_type=business_type,
        risk_group=risk_group,
    )
    stance = decision["stance"]

    memo = _investment_memo(
        brain_result.get("ticker", ""), stance, valuation, pricing_context,
        valuation_freshness, business_type, investment_score, confidence,
    )

    out: Dict[str, Any] = {
        "ticker": brain_result.get("ticker"),
        "as_of_date": brain_result.get("as_of_date"),
        "model_name": brain_result.get("model_name"),
        "model_version": brain_result.get("model_version"),
        "brain_status": brain_result.get("status"),
    }
    out.update(investment_horizon_meta())
    out.update({
        "business_type": business_type,
        "valuation": valuation,
        "business_quality": {
            "group_scores": group_scores,
            "moat_quality_score": group_scores.get("quality"),
            "balance_sheet_score": group_scores.get("risk"),
        },
        "pricing_context": pricing_context,
        "valuation_freshness": valuation_freshness,
        "risk_committee": {
            "max_allowed_stance": decision["max_allowed_stance"],
            "stance_reason": decision["stance_reason"],
            "confidence": confidence,
        },
        "final": {
            "investment_score": investment_score,
            "stance": stance,
            "confidence": confidence,
            "horizon": "1_to_3_years",
        },
        "memo": memo,
        "guardrail": INVESTMENT_GUARDRAIL,
        "disclaimer": DISCLAIMER,
    })
    return out
