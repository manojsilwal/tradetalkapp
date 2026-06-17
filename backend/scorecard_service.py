"""Shared scorecard compute for router + decision-terminal embed (compute-only, no ledger)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from backend.connectors.scorecard_data import fetch_scorecard_data
from backend.scorecard import score_single
from backend.schemas import DataFreshness, TerminalScorecardSummary

_FRAMING_NOTE = (
    "Single-name preview (balanced preset). Not a buy/sell rating — "
    "compare multiple tickers on /scorecard for relative rankings."
)


def _weighted_score(d: Dict[str, float]) -> float:
    total_w = sum(d.values()) if d else 0.0
    if total_w <= 0:
        return 0.0
    return round(sum(float(k) * float(v) for k, v in d.items()) / total_w, 4)


async def build_terminal_scorecard_summary(
    ticker: str,
    *,
    preset: str = "balanced",
    skip_llm_scores: bool = True,
) -> Optional[TerminalScorecardSummary]:
    """
    Compute-only scorecard summary for /decision-terminal embed.
    Does NOT emit to the Decision-Outcome Ledger.
    """
    from backend.routers.scorecard import (
        _data_to_scorecard_input,
        _fetch_subjective_scores,
        _fetch_verdicts_single,
        _scorecard_freshness,
    )

    sym = ticker.strip().upper()
    data = await fetch_scorecard_data(sym)
    sitg_by_ticker, exec_by_ticker = await _fetch_subjective_scores(
        [data], skip_llm=skip_llm_scores
    )
    inp = _data_to_scorecard_input(
        data,
        sitg_score=sitg_by_ticker[data.ticker]["sitg_score"],
        sitg_archetype=sitg_by_ticker[data.ticker].get("archetype", ""),
        exec_score=exec_by_ticker[data.ticker]["exec_score"],
    )
    row = score_single(inp, preset=preset)
    verdicts = await _fetch_verdicts_single(row, preset, skip_llm=skip_llm_scores)
    v = verdicts.get(row.ticker, {"verdict": "Balanced", "one_line_reason": ""})

    fresh_raw = _scorecard_freshness()
    fresh: Optional[DataFreshness] = None
    if fresh_raw:
        try:
            fresh = DataFreshness.model_validate(fresh_raw)
        except Exception:
            fresh = None

    ret_w = _weighted_score(row.return_score.__dict__)
    risk_w = _weighted_score(row.risk_score.__dict__)

    return TerminalScorecardSummary(
        ticker=row.ticker,
        preset=preset,
        is_comparative=False,
        ratio=row.ratio,
        signal=row.signal,
        action=row.action,
        verdict=str(v.get("verdict", "Balanced")),
        quadrant=row.quadrant,
        return_score_weighted=ret_w,
        risk_score_weighted=risk_w,
        framing_note=_FRAMING_NOTE,
        one_line_reason=str(v.get("one_line_reason", "")),
        data_freshness=fresh,
    )
