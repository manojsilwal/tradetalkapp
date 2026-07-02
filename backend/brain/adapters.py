"""Map the brain's serving contract into each legacy surface's response shape.

The brain emits a model-agnostic contract (``serve_ticker`` / reflex output):
``outperform_probability`` (0-1), ``composite_score`` (0-100), ``signal_scores``,
``risk_score``, ``recommendation`` (constructive/neutral/cautious), ``valuation``,
``reconciliation``, ``drivers``. These pure functions translate that into the
verdict vocabularies the existing UIs and ledger expect, so the frontend is
untouched during the cutover.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Canonical 5-level verdict thresholds on outperform_probability.
_P_STRONG_BUY = 0.66
_P_BUY = 0.56
_P_SELL = 0.44
_P_STRONG_SELL = 0.34

STALE_ANCHOR_MOVE_PCT = 0.15


def anchor_move_pct(brain_result: Dict) -> Optional[float]:
    """Absolute move between nightly base_price and live spot (0–1 scale)."""
    fresh = brain_result.get("freshness") or {}
    if fresh.get("move_since_base") is not None:
        return abs(float(fresh["move_since_base"]))
    val = brain_result.get("valuation") or {}
    base = val.get("base_price")
    live = val.get("live_price")
    if base and live:
        try:
            b = float(base)
            if b > 0:
                return abs(float(live) / b - 1.0)
        except (TypeError, ValueError):
            return None
    return None


def _downgrade_extreme_verdict(verdict: str) -> str:
    if verdict == "Strong Buy":
        return "Buy"
    if verdict == "Strong Sell":
        return "Sell"
    return verdict


def _block(brain_result: Dict) -> Dict:
    """Prefer the live-adjusted block; fall back to the base contract."""
    return brain_result.get("live") or brain_result.get("base") or {}


def outperform_probability(brain_result: Dict) -> Optional[float]:
    return _block(brain_result).get("outperform_probability")


def composite_score(brain_result: Dict) -> float:
    val = _block(brain_result).get("composite_score")
    return float(val) if val is not None else 50.0


def verdict_5(brain_result: Dict) -> str:
    """Canonical title-case 5-level verdict from outperformance probability."""
    p = outperform_probability(brain_result)
    if p is None:
        raw = "Hold"
    elif p >= _P_STRONG_BUY:
        raw = "Strong Buy"
    elif p >= _P_BUY:
        raw = "Buy"
    elif p > _P_SELL:
        raw = "Hold"
    elif p > _P_STRONG_SELL:
        raw = "Sell"
    else:
        raw = "Strong Sell"

    status = (brain_result.get("status") or "").upper()
    if status in ("INVALID", "INVALID_INPUT", "STALE") and raw in ("Strong Buy", "Strong Sell"):
        return _downgrade_extreme_verdict(raw)

    move = anchor_move_pct(brain_result)
    if move is not None and move > STALE_ANCHOR_MOVE_PCT and raw in ("Strong Buy", "Strong Sell"):
        return _downgrade_extreme_verdict(raw)
    return raw


def verdict_4(brain_result: Dict) -> str:
    """4-level verdict (daily brief collapses Strong Sell into Sell)."""
    v = verdict_5(brain_result)
    return "Sell" if v == "Strong Sell" else v


def swarm_verdict(brain_result: Dict) -> str:
    """Uppercase verdict vocabulary used by SwarmConsensus."""
    return verdict_5(brain_result).upper()


def _options_flow_score(aggregates: Dict[str, Any]) -> float:
    """Map options aggregates to a 0-100 signal score (higher = more bullish)."""
    score = 50.0
    pcr = aggregates.get("put_call_volume_ratio")
    if pcr is not None:
        if pcr >= 1.3:
            score -= min(25.0, (pcr - 1.0) * 30.0)
        elif pcr <= 0.7:
            score += min(25.0, (1.0 - pcr) * 30.0)
    bias = aggregates.get("net_premium_bias")
    if bias == "bullish":
        score += 10.0
    elif bias == "bearish":
        score -= 10.0
    unusual = aggregates.get("unusual_activity_score")
    if unusual is not None and pcr is not None and pcr >= 1.1:
        score -= min(10.0, float(unusual) * 0.1)
    return round(max(0.0, min(100.0, score)), 2)


def inject_options_signal(brain_result: Dict, aggregates: Optional[Dict[str, Any]]) -> Dict:
    """Merge options_flow bar into live/base signal_scores when aggregates present."""
    if not brain_result or not aggregates or aggregates.get("available") is False:
        return brain_result
    score = _options_flow_score(aggregates)
    out = dict(brain_result)
    for key in ("live", "base"):
        block = out.get(key)
        if isinstance(block, dict):
            ss = dict(block.get("signal_scores") or {})
            ss["options_flow"] = score
            block = dict(block)
            block["signal_scores"] = ss
            out[key] = block
    return out


def one_line_reason(brain_result: Dict) -> str:
    drivers = _block(brain_result).get("drivers") or {}
    support = drivers.get("supporting") or []
    detract = drivers.get("detracting") or []
    verdict = verdict_5(brain_result)
    ss = _block(brain_result).get("signal_scores") or {}
    opts = ss.get("options_flow")
    opts_note = None
    if opts is not None and abs(float(opts) - 50.0) >= 15.0:
        if opts >= 65:
            opts_note = "bullish options flow"
        elif opts <= 35:
            opts_note = "bearish put/call skew"
    if support and verdict in ("Strong Buy", "Buy"):
        bits = support[:2]
        if opts_note:
            bits = [opts_note] + bits
        return f"{verdict}: " + ", ".join(bits[:2])
    if detract and verdict in ("Strong Sell", "Sell"):
        bits = detract[:2]
        if opts_note:
            bits = [opts_note] + bits
        return f"{verdict}: " + ", ".join(bits[:2])
    bits = ([opts_note] if opts_note else []) + support[:1] + detract[:1]
    return f"{verdict}: " + (", ".join(bits) if bits else "balanced signals")


def to_actionable_row(brain_result: Dict) -> Dict[str, Any]:
    score = round(composite_score(brain_result), 2)
    verdict = verdict_5(brain_result)
    return {
        "score": score,
        "verdict": verdict,
        "actionable": verdict in ("Strong Buy", "Buy", "Sell"),
    }


def to_daily_brief_verdict(brain_result: Dict) -> Dict[str, Any]:
    block = _block(brain_result)
    return {
        "verdict": verdict_4(brain_result),
        "one_line_reason": one_line_reason(brain_result),
        "verdict_tier": "brain",
        # Numeric fields so the UI can display the brain score alongside the verdict.
        "outperform_probability": block.get("outperform_probability"),
        "composite_score": block.get("composite_score"),
        "signal_scores": block.get("signal_scores"),
        "confidence_score": brain_result.get("confidence_score"),
        "live_price": block.get("live_price"),
        "price_source": brain_result.get("price_source"),
        "brain_status": brain_result.get("status"),
    }


def to_scorecard_fields(brain_result: Dict) -> Dict[str, Any]:
    """Map to scorecard's signal/action/quadrant/verdict fields."""
    verdict = verdict_5(brain_result)
    signal_map = {
        "Strong Buy": "Strong Buy", "Buy": "Buy", "Hold": "Hold",
        "Sell": "Sell", "Strong Sell": "Strong Sell",
    }
    action = "Buy" if verdict in ("Strong Buy", "Buy") else (
        "Avoid" if verdict in ("Strong Sell", "Sell") else "Watch")
    recon = brain_result.get("reconciliation") or {}
    quadrant = recon.get("quadrant") or "neutral"
    op = outperform_probability(brain_result)
    return {
        "signal": signal_map[verdict],
        "action": action,
        "quadrant": quadrant,
        "verdict": verdict,
        "one_line_reason": one_line_reason(brain_result),
        "ratio": round(op, 4) if op is not None else None,
    }


def to_decision_terminal_headline(brain_result: Dict) -> Dict[str, Any]:
    """Headline verdict fragment for the decision terminal."""
    val = brain_result.get("valuation") or {}
    ts = (brain_result.get("timeseries") or {}).get("live") or {}
    return {
        "headline_verdict": verdict_5(brain_result),
        "fusion_note": "Verdict generated by the finance brain (model "
                       f"{brain_result.get('model_version', '?')}).",
        "outperform_probability": outperform_probability(brain_result),
        "composite_score": composite_score(brain_result),
        "confidence_pct": round((brain_result.get("confidence_score") or 0.0) * 100, 1),
        "intrinsic_value_mid": val.get("intrinsic_value_mid"),
        "dcf_upside": val.get("dcf_upside_live", val.get("dcf_upside_at_base")),
        "timeseries_expected_return": ts.get("expected_return"),
    }
