"""LLM explanation layer guardrail + deterministic explainer.

The LLM's job is to *explain* the brain's numbers, never to invent them
(docs Rule 12). ``verify_grounding`` checks that every number an LLM emits traces
back to the structured prediction payload; ``generate_explanation`` produces a
deterministic, always-grounded narrative from the contract (a safe fallback and
a golden reference for tests).
"""
from __future__ import annotations

import re
from typing import Dict, List

# Matches standalone integers/decimals only. The boundaries reject digits that
# are part of an identifier/token (e.g. the "075" in ticker "T075" or "1" in the
# model version "v1") so we don't false-flag those as ungrounded numbers.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])-?\d+(?:\.\d+)?(?![A-Za-z0-9])(?!-[A-Za-z])")


def _extract_numbers(text: str) -> List[float]:
    return [float(m.group()) for m in _NUMBER_RE.finditer(text)]


def _allowed_values(payload, acc: List[float]) -> None:
    """Recursively collect every numeric value from the payload."""
    if isinstance(payload, bool):
        return
    if isinstance(payload, (int, float)):
        acc.append(float(payload))
    elif isinstance(payload, dict):
        for v in payload.values():
            _allowed_values(v, acc)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _allowed_values(v, acc)


def _expand(values: List[float]) -> List[float]:
    """Add percentage forms so '0.62' and '62' both verify."""
    out = set()
    for a in values:
        out.add(round(a, 4))
        out.add(round(a, 2))
        out.add(float(round(a)))
        if -1.0 <= a <= 1.0:
            out.add(round(a * 100.0, 2))
            out.add(float(round(a * 100.0)))
    return sorted(out)


def verify_grounding(text: str, payload: Dict, tol: float = 0.6) -> Dict:
    """Return {grounded, ungrounded_numbers, checked} for an LLM explanation."""
    allowed: List[float] = []
    _allowed_values(payload, allowed)
    allowed_expanded = _expand(allowed)

    ungrounded: List[float] = []
    nums = _extract_numbers(text)
    for x in nums:
        ok = any(abs(x - a) <= max(tol, abs(a) * 0.02) for a in allowed_expanded)
        if not ok:
            ungrounded.append(x)
    return {
        "grounded": len(ungrounded) == 0,
        "ungrounded_numbers": ungrounded,
        "checked": len(nums),
    }


def generate_explanation(contract: Dict) -> str:
    """Deterministic, fully-grounded narrative built only from contract numbers."""
    p_pct = round(contract["outperform_probability"] * 100)
    conf_pct = round(contract.get("confidence_score", 0.0) * 100)
    comp = contract.get("composite_score")
    ss = contract.get("signal_scores", {})
    horizon = contract.get("horizon_days")
    risk_pct = round(contract.get("risk_score", 0.0) * 100)

    parts: List[str] = []
    parts.append(
        f"{contract['ticker']}: model {contract['model_version']} estimates a "
        f"{p_pct}% probability of outperforming its sector over the next "
        f"{horizon} trading days ({contract['recommendation']}; confidence {conf_pct}%)."
    )
    if comp is not None:
        parts.append(f"Composite score is {comp}.")
    bits = [f"{g} {ss[g]}" for g in ("momentum", "quality", "valuation") if ss.get(g) is not None]
    if bits:
        parts.append("Signal scores: " + ", ".join(bits) + ".")
    drivers = contract.get("drivers", {})
    if drivers.get("supporting"):
        parts.append("Supporting: " + "; ".join(drivers["supporting"]) + ".")
    if drivers.get("detracting"):
        parts.append("Detracting: " + "; ".join(drivers["detracting"]) + ".")
    parts.append(f"Risk score {risk_pct}%. Not financial advice.")
    return " ".join(parts)


def generate_reflex_explanation(reflex: Dict) -> str:
    """Deterministic, grounded narrative of the base -> current bridge.

    Every number is drawn from the reflex payload, so verify_grounding(text,
    reflex) passes by construction. This is the safe fallback / golden reference
    the LLM explanation layer must not contradict.
    """
    base = reflex.get("base", {})
    ticker = reflex.get("ticker")
    status = reflex.get("status")
    base_p = round(base.get("outperform_probability", 0.0) * 100)
    base_rec = base.get("recommendation", "neutral")

    if status in ("INVALID", "STALE", "INVALID_INPUT"):
        reasons = ", ".join(reflex.get("reasons", [])) or "anchor no longer valid"
        return (
            f"{ticker}: base model {reflex.get('model_version')} saw a {base_p}% "
            f"probability ({base_rec}), but the live view is withheld because "
            f"{reasons}. A fresh recompute has been requested. Not financial advice."
        )

    live = reflex.get("live", {})
    val_block = reflex.get("valuation", {})
    fresh = reflex.get("freshness", {})
    live_p = round(live.get("outperform_probability", 0.0) * 100)
    live_rec = live.get("recommendation", "neutral")
    move_pct = round(fresh.get("move_since_base", 0.0) * 100)
    conf_pct = round(reflex.get("confidence_score", 0.0) * 100)

    parts: List[str] = [
        f"{ticker}: base model {reflex.get('model_version')} estimated a {base_p}% "
        f"outperformance probability ({base_rec})."
    ]
    parts.append(
        f"After a {move_pct}% move to {val_block.get('live_price')}, the live re-run "
        f"is {live_p}% ({live_rec})."
    )
    dcf_b = val_block.get("dcf_upside_at_base")
    dcf_l = val_block.get("dcf_upside_live")
    if dcf_b is not None and dcf_l is not None:
        parts.append(
            f"With intrinsic value fixed at {val_block.get('intrinsic_value_mid')}, "
            f"DCF upside moved from {round(dcf_b * 100)}% to {round(dcf_l * 100)}%."
        )
    parts.append(f"Live confidence {conf_pct}%. Not financial advice.")
    return " ".join(parts)
