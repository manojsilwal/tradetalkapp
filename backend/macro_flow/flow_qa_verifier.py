"""Deterministic QA arbitration between quant flow and qual fundamentals."""
from __future__ import annotations

from typing import Any, Dict


def verify_flow_qa(
    *,
    flow_score: float,
    weighted_qual: float,
    fundamental_band: str,
) -> Dict[str, Any]:
    """
    Returns verdict in
    durable|speculative|watch|low_conviction|outflow
    """
    fs = float(flow_score)
    q = float(weighted_qual)
    conflict = (fs > 0.15 and q < 0.45) or (fs < -0.15 and q > 0.6)

    if fs < -0.25:
        verdict = "outflow"
        confidence = 0.72
        notes = "Quant flow negative vs SPY/CMF blend; treat as distribution risk."
    elif fs < -0.05 and q < 0.45:
        verdict = "low_conviction"
        confidence = 0.55
        notes = "Weak flow and weak fundamentals — avoid chasing."
    elif fs > 0.1 and q > 0.58:
        verdict = "durable"
        confidence = 0.78
        notes = "Positive flow with supportive fundamentals."
    elif fs > 0.12 and q < 0.5:
        verdict = "speculative"
        confidence = 0.62
        notes = "Strong flow but fundamentals lag — momentum / positioning risk."
    elif abs(fs) <= 0.08:
        verdict = "watch"
        confidence = 0.45
        notes = "Flat flow — wait for CMF/RS confirmation."
    else:
        verdict = "watch"
        confidence = 0.52
        notes = f"Mixed: flow={fs:.2f}, qual_band={fundamental_band}."

    return {
        "qa_verdict": verdict,
        "confidence": float(confidence),
        "conflict_flag": bool(conflict),
        "notes": notes,
    }
