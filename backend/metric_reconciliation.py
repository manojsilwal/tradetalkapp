"""Server-side reconciliation of verdict / valuation / roadmap / scorecard signals."""
from __future__ import annotations

from typing import List, Optional

from backend.metric_primitives import verdict_tone
from backend.schemas import (
    ReconciliationSignal,
    TerminalReconciliationPanel,
    TerminalScorecardSummary,
)

_OPPOSING = {
    "strong_positive": {"negative", "caution"},
    "positive": {"negative"},
    "neutral": set(),
    "caution": {"strong_positive", "positive"},
    "negative": {"strong_positive", "positive"},
}


def _scorecard_tone(signal: str, verdict: str) -> str:
    blob = f"{signal} {verdict}".upper()
    if any(x in blob for x in ("EXCEPTIONAL", "STRONG BUY", "STRONG_BUY")):
        return "strong_positive"
    if any(x in blob for x in ("FAVORABLE", "BUY")):
        return "positive"
    if any(x in blob for x in ("CAUTION", "STRETCHED")):
        return "caution"
    if any(x in blob for x in ("AVOID", "SELL")):
        return "negative"
    return verdict_tone(signal or verdict)


def build_reconciliation(
    *,
    headline_verdict: str,
    fusion_note: str = "",
    pct_vs_average: Optional[float] = None,
    gauge_label: str = "",
    predicted_cagr_base_pct: Optional[float] = None,
    swarm_rejected: bool = False,
    scorecard_summary: Optional[TerminalScorecardSummary] = None,
) -> TerminalReconciliationPanel:
    primary_tone = verdict_tone(headline_verdict)
    supporting: List[ReconciliationSignal] = []
    conflicting: List[ReconciliationSignal] = []

    def _add(source: str, label: str, detail: str = "") -> None:
        tone = verdict_tone(label)
        chip = ReconciliationSignal(source=source, label=label, tone=tone, detail=detail)
        if tone == primary_tone or tone == "neutral":
            supporting.append(chip)
        elif tone in _OPPOSING.get(primary_tone, set()):
            conflicting.append(chip)
        elif primary_tone in _OPPOSING.get(tone, set()):
            conflicting.append(chip)
        else:
            supporting.append(chip)

    if gauge_label:
        _add("valuation", gauge_label, f"Fair value gap: {pct_vs_average:+.1f}%" if pct_vs_average is not None else "")

    if predicted_cagr_base_pct is not None:
        cagr_label = f"Roadmap base +{predicted_cagr_base_pct:.1f}% CAGR (3Y)" if predicted_cagr_base_pct >= 0 else f"Roadmap base {predicted_cagr_base_pct:.1f}% CAGR (3Y)"
        _add("roadmap", cagr_label)

    if swarm_rejected:
        conflicting.append(
            ReconciliationSignal(
                source="verdict",
                label="Swarm rejected",
                tone="caution",
                detail="Multi-agent swarm did not reach consensus.",
            )
        )

    if scorecard_summary is not None:
        sc_tone = _scorecard_tone(scorecard_summary.signal, scorecard_summary.verdict)
        chip = ReconciliationSignal(
            source="scorecard",
            label=f"Risk-return profile: {scorecard_summary.signal}",
            tone=sc_tone,
            detail=scorecard_summary.framing_note or "",
        )
        if sc_tone == primary_tone or sc_tone == "neutral":
            supporting.append(chip)
        elif sc_tone in _OPPOSING.get(primary_tone, set()) or primary_tone in _OPPOSING.get(sc_tone, set()):
            conflicting.append(chip)
        else:
            supporting.append(chip)

    note_parts: List[str] = []
    if conflicting:
        val_conflict = any(c.source == "valuation" for c in conflicting)
        roadmap_conflict = any(c.source == "roadmap" for c in conflicting)
        sc_conflict = any(c.source == "scorecard" for c in conflicting)
        if val_conflict and primary_tone in ("positive", "strong_positive"):
            note_parts.append(
                "Debate leans bullish while valuation models suggest the stock trades above fair value."
            )
        if roadmap_conflict and pct_vs_average is not None and pct_vs_average < -5:
            note_parts.append(
                f"Roadmap base case implies {predicted_cagr_base_pct:+.1f}% CAGR over 3Y while valuation shows overvaluation."
                if predicted_cagr_base_pct is not None
                else "Roadmap growth expectations conflict with current valuation."
            )
        if sc_conflict and scorecard_summary is not None:
            note_parts.append(scorecard_summary.framing_note)
        if fusion_note:
            note_parts.append(fusion_note)
        reconciliation_note = " ".join(note_parts).strip()
        if not reconciliation_note and conflicting:
            reconciliation_note = (
                "Multiple signals disagree — review valuation, roadmap, and risk-return profile together."
            )
    else:
        reconciliation_note = fusion_note or ""

    # Roadmap vs valuation (H6)
    if (
        predicted_cagr_base_pct is not None
        and predicted_cagr_base_pct > 0
        and pct_vs_average is not None
        and pct_vs_average < -5
        and not any(c.source == "roadmap" for c in conflicting)
    ):
        conflicting.append(
            ReconciliationSignal(
                source="roadmap",
                label=f"Growth +{predicted_cagr_base_pct:.1f}% CAGR vs overvalued gauge",
                tone="caution",
                detail="Positive roadmap CAGR while valuation gauge shows premium.",
            )
        )
        if not reconciliation_note:
            reconciliation_note = (
                "Roadmap implies positive growth while valuation models flag the stock as overvalued."
            )

    return TerminalReconciliationPanel(
        primary_headline=headline_verdict,
        supporting_signals=supporting,
        conflicting_signals=conflicting,
        reconciliation_note=reconciliation_note,
    )
