"""
Risk-Return-Ratio Scorecard HTTP surface.

Routes
------
``POST /scorecard/compare`` — primary entrypoint. Takes a basket of 2-10
tickers plus an investor-type preset, enriches each ticker with the data
connector, asks the LLM personas for the subjective scores (SITG +
Execution Risk), runs the deterministic math in :mod:`backend.scorecard`,
then asks a light verdict persona for a one-sentence callout per row.

``GET /scorecard/{ticker}`` — single-ticker convenience route that uses
industry-median denominators via :func:`backend.scorecard.score_single`.

``GET /scorecard/presets`` — returns the four preset weight tables so the
frontend can display them.

Rate-limited under the ``expensive`` bucket (same as /debate and /analyze).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..auth import get_optional_user
from ..connectors.scorecard_data import ScorecardData, fetch_basket, fetch_scorecard_data
from ..deps import llm_client
from ..rate_limiter import rate_limit
from ..scorecard import (
    PRESETS,
    BasketResult,
    ScorecardInput,
    apply_situational_adjustments,
    classify_quadrant,
    interpret_ratio,
    resolve_weights,
    score_basket,
    score_single,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scorecard", tags=["scorecard"])

_rl_expensive = rate_limit("expensive")


# ── Ingress / egress models ──────────────────────────────────────────────────

class ScorecardCompareRequest(BaseModel):
    """Basket scoring request body."""
    tickers: List[str] = Field(..., min_length=1, max_length=10)
    preset: str = Field("balanced", description="growth | value | income | balanced")
    weights_override: Optional[Dict[str, float]] = Field(
        default=None,
        description="Sparse override map on preset weights (keys: w1..w9).",
    )
    situational_flags: Optional[Dict[str, bool]] = Field(
        default=None,
        description="Step-7 adjustment flags (e.g. 'utilities_vs_industrials').",
    )
    skip_llm_scores: bool = Field(
        default=False,
        description=(
            "When True, default exec_risk=5 and SITG=3 (fallback templates) instead "
            "of calling the LLM personas. Used by tests and cheap previews."
        ),
    )

    @field_validator("tickers")
    @classmethod
    def _uppercase_tickers(cls, v: List[str]) -> List[str]:
        out = []
        seen = set()
        for t in v:
            if not t or not isinstance(t, str):
                continue
            tt = t.strip().upper()
            if tt and tt not in seen and 1 <= len(tt) <= 10:
                out.append(tt)
                seen.add(tt)
        if not out:
            raise ValueError("no valid tickers in request")
        return out

    @field_validator("preset")
    @classmethod
    def _preset_known(cls, v: str) -> str:
        key = (v or "balanced").strip().lower()
        if key not in PRESETS:
            raise ValueError(
                f"unknown preset {v!r}; expected one of {sorted(PRESETS.keys())}"
            )
        return key


class ScorecardRowOut(BaseModel):
    ticker: str
    ceo_name: str
    sitg_archetype: str
    return_score: Dict[str, float]
    risk_score: Dict[str, float]
    ratio: float
    sitg_boost: float
    signal: str
    action: str
    quadrant: str
    verdict: str
    one_line_reason: str
    # Original raw data bundle echoed back for UI / audit
    inputs: Dict[str, Any]


class ScorecardResponse(BaseModel):
    preset: str
    weights: Dict[str, float]
    denominators: Dict[str, float]
    rows: List[ScorecardRowOut]
    notes: List[str] = Field(default_factory=list)


# ── Handlers ─────────────────────────────────────────────────────────────────

@router.get("/presets")
def list_presets() -> Dict[str, Dict[str, float]]:
    """Return the four preset weight tables for the frontend selector."""
    return {name: w.as_dict() for name, w in PRESETS.items()}


@router.post("/compare", response_model=ScorecardResponse, dependencies=[Depends(_rl_expensive)])
async def compare_scorecard(
    req: ScorecardCompareRequest,
    _user: Optional[Dict[str, Any]] = Depends(get_optional_user),
) -> ScorecardResponse:
    """Score a basket of tickers under the chosen investor-type preset."""
    try:
        data_rows = await fetch_basket(req.tickers)
    except Exception as e:
        logger.exception("[scorecard] data fetch failed")
        raise HTTPException(status_code=502, detail=f"data fetch failed: {e}") from e

    # Subjective scores (LLM personas) — parallel per ticker.
    sitg_by_ticker, exec_by_ticker = await _fetch_subjective_scores(
        data_rows, skip_llm=req.skip_llm_scores
    )

    inputs = [
        _data_to_scorecard_input(
            d,
            sitg_score=sitg_by_ticker[d.ticker]["sitg_score"],
            sitg_archetype=sitg_by_ticker[d.ticker].get("archetype", ""),
            exec_score=exec_by_ticker[d.ticker]["exec_score"],
        )
        for d in data_rows
    ]

    basket = score_basket(
        inputs,
        preset=req.preset,
        weights_override=req.weights_override,
        situational_flags=req.situational_flags,
    )

    verdicts = await _fetch_verdicts(basket, skip_llm=req.skip_llm_scores, preset=req.preset)

    notes: List[str] = []
    for d in data_rows:
        if d.fields_missing:
            notes.append(f"{d.ticker}: missing fields — {', '.join(d.fields_missing)}")

    rows_out: List[ScorecardRowOut] = []
    for row, data in zip(basket.rows, data_rows):
        v = verdicts.get(row.ticker, {"verdict": "Balanced", "one_line_reason": ""})
        rows_out.append(
            ScorecardRowOut(
                ticker=row.ticker,
                ceo_name=row.ceo_name,
                sitg_archetype=row.sitg_archetype,
                return_score=_round_dict(row.return_score.__dict__),
                risk_score=_round_dict(row.risk_score.__dict__),
                ratio=row.ratio,
                sitg_boost=row.sitg_boost,
                signal=row.signal,
                action=row.action,
                quadrant=row.quadrant,
                verdict=str(v.get("verdict", "Balanced")),
                one_line_reason=str(v.get("one_line_reason", "")),
                inputs=data.to_dict(),
            )
        )

    return ScorecardResponse(
        preset=basket.preset,
        weights=basket.weights.as_dict(),
        denominators=basket.denominators,
        rows=rows_out,
        notes=notes,
    )


@router.get("/{ticker}", response_model=ScorecardRowOut, dependencies=[Depends(_rl_expensive)])
async def single_ticker_scorecard(
    ticker: str,
    preset: str = Query("balanced"),
    skip_llm_scores: bool = Query(False),
    _user: Optional[Dict[str, Any]] = Depends(get_optional_user),
) -> ScorecardRowOut:
    """
    Single-ticker convenience route. Uses neutral "self" denominators (Step 1
    fallback) because industry medians aren't wired in yet. Best for UI
    previews; use ``/compare`` for a proper peer-normalized score.
    """
    sym = ticker.strip().upper()
    if not sym or len(sym) > 10:
        raise HTTPException(status_code=400, detail=f"invalid ticker {ticker!r}")
    preset_key = (preset or "balanced").strip().lower()
    if preset_key not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown preset {preset!r}; expected one of {sorted(PRESETS.keys())}",
        )

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
    row = score_single(inp, preset=preset_key)

    verdicts = await _fetch_verdicts_single(row, preset_key, skip_llm=skip_llm_scores)
    v = verdicts.get(row.ticker, {"verdict": "Balanced", "one_line_reason": ""})

    return ScorecardRowOut(
        ticker=row.ticker,
        ceo_name=row.ceo_name,
        sitg_archetype=row.sitg_archetype,
        return_score=_round_dict(row.return_score.__dict__),
        risk_score=_round_dict(row.risk_score.__dict__),
        ratio=row.ratio,
        sitg_boost=row.sitg_boost,
        signal=row.signal,
        action=row.action,
        quadrant=row.quadrant,
        verdict=str(v.get("verdict", "Balanced")),
        one_line_reason=str(v.get("one_line_reason", "")),
        inputs=data.to_dict(),
    )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _data_to_scorecard_input(
    d: ScorecardData,
    *,
    sitg_score: float,
    sitg_archetype: str,
    exec_score: float,
) -> ScorecardInput:
    return ScorecardInput(
        ticker=d.ticker,
        eps_growth_pct=d.eps_growth_pct,
        revenue_growth_pct=d.revenue_growth_pct,
        pt_upside_pct=d.pt_upside_pct,
        dividend_yield_pct=d.dividend_yield_pct,
        forward_pe=d.forward_pe,
        historical_avg_pe=d.historical_avg_pe,
        beta=d.beta,
        exec_risk_score=float(exec_score),
        debt_to_equity=d.debt_to_equity,
        sitg_score=float(sitg_score),
        ceo_name=d.ceo_name,
        sitg_archetype=sitg_archetype,
    )


async def _fetch_subjective_scores(
    data_rows: List[ScorecardData],
    *,
    skip_llm: bool,
) -> tuple[Dict[str, dict], Dict[str, dict]]:
    """
    Fan out SITG + Execution-Risk persona calls in parallel. On any per-ticker
    failure, uses the persona's fallback template so the basket still scores.
    """
    if skip_llm:
        sitg_map = {
            d.ticker: {"sitg_score": 3.0, "archetype": "Most S&P 500 CEOs"}
            for d in data_rows
        }
        exec_map = {d.ticker: {"exec_score": 5.0, "profile_tier": "mid_growth"} for d in data_rows}
        return sitg_map, exec_map

    async def _score_sitg(d: ScorecardData) -> tuple[str, dict]:
        try:
            ctx = {
                "ticker": d.ticker,
                "company_name": d.company_name,
                "sector": d.sector,
                "industry": d.industry,
                "ceo_name": d.ceo_name,
                "insider_buy_count_12m": d.insider_buy_count_12m,
                "insider_sell_count_12m": d.insider_sell_count_12m,
                "insider_net_shares_12m": d.insider_net_shares_12m,
                "held_percent_insiders": d.held_percent_insiders,
            }
            out = await llm_client.generate_sitg_score(d.ticker, ctx)
            score = float(out.get("sitg_score", 3.0))
            return d.ticker, {
                "sitg_score": max(0.0, min(10.0, score)),
                "archetype": str(out.get("archetype") or ""),
                "raw": out,
            }
        except Exception as e:
            logger.warning("[scorecard] sitg scorer failed for %s: %s", d.ticker, e)
            return d.ticker, {"sitg_score": 3.0, "archetype": "Most S&P 500 CEOs"}

    async def _score_exec(d: ScorecardData) -> tuple[str, dict]:
        try:
            ctx = {
                "ticker": d.ticker,
                "company_name": d.company_name,
                "sector": d.sector,
                "industry": d.industry,
                "eps_growth_pct": d.eps_growth_pct,
                "revenue_growth_pct": d.revenue_growth_pct,
                "beta": d.beta,
                "debt_to_equity": d.debt_to_equity,
                "forward_pe": d.forward_pe,
            }
            out = await llm_client.generate_execution_risk_score(d.ticker, ctx)
            score = float(out.get("exec_score", 5.0))
            return d.ticker, {
                "exec_score": max(1.0, min(10.0, score)),
                "profile_tier": str(out.get("profile_tier") or "mid_growth"),
                "raw": out,
            }
        except Exception as e:
            logger.warning("[scorecard] exec scorer failed for %s: %s", d.ticker, e)
            return d.ticker, {"exec_score": 5.0, "profile_tier": "mid_growth"}

    sitg_task = asyncio.gather(*[_score_sitg(d) for d in data_rows])
    exec_task = asyncio.gather(*[_score_exec(d) for d in data_rows])
    sitg_results, exec_results = await asyncio.gather(sitg_task, exec_task)
    return dict(sitg_results), dict(exec_results)


async def _fetch_verdicts(
    basket: BasketResult, *, skip_llm: bool, preset: str
) -> Dict[str, dict]:
    if skip_llm:
        return {r.ticker: {"verdict": _default_verdict_label(r.signal), "one_line_reason": ""} for r in basket.rows}

    async def _one(row) -> tuple[str, dict]:
        try:
            ctx = {
                "ticker": row.ticker,
                "preset": preset,
                "ratio": row.ratio,
                "signal": row.signal,
                "return_score": round(row.return_score.weighted, 2),
                "risk_score": round(row.risk_score.weighted, 2),
                "sitg_score": round(row.return_score.sitg_score, 2),
                "sitg_boost": row.sitg_boost,
                "reason_hint": _reason_hint(row),
            }
            out = await llm_client.generate_scorecard_verdict(row.ticker, ctx)
            return row.ticker, {
                "verdict": str(out.get("verdict") or _default_verdict_label(row.signal)),
                "one_line_reason": str(out.get("one_line_reason") or ""),
            }
        except Exception as e:
            logger.warning("[scorecard] verdict writer failed for %s: %s", row.ticker, e)
            return row.ticker, {"verdict": _default_verdict_label(row.signal), "one_line_reason": ""}

    results = await asyncio.gather(*[_one(r) for r in basket.rows])
    return dict(results)


async def _fetch_verdicts_single(row, preset: str, *, skip_llm: bool) -> Dict[str, dict]:
    class _B:
        rows = [row]
    fake_basket: Any = _B()
    return await _fetch_verdicts(fake_basket, skip_llm=skip_llm, preset=preset)


def _reason_hint(row) -> str:
    """Short string describing the dominant driver of this row's ratio."""
    rs = row.risk_score
    ret = row.return_score
    drivers = []
    if rs.pe_stretch_score >= 7.0:
        drivers.append(f"PE stretch score {rs.pe_stretch_score:.1f}")
    if rs.beta_score >= 7.0:
        drivers.append(f"beta score {rs.beta_score:.1f}")
    if rs.exec_score >= 7.0:
        drivers.append(f"execution risk {rs.exec_score:.1f}/10")
    if rs.leverage_score >= 7.0:
        drivers.append(f"leverage score {rs.leverage_score:.1f}")
    if ret.sitg_score >= 7.0:
        drivers.append(f"SITG {ret.sitg_score:.1f}/10 lift")
    if ret.pt_upside_score >= 7.0:
        drivers.append(f"PT upside score {ret.pt_upside_score:.1f}")
    if not drivers:
        drivers.append(f"ratio {row.ratio:.2f}")
    return "; ".join(drivers)


def _default_verdict_label(signal: str) -> str:
    """Map Step-3 interpretation signal to the verdict enum used by the persona."""
    m = {
        "Exceptional": "Strong",
        "Strong buy": "Strong",
        "Favorable": "Favorable",
        "Balanced": "Balanced",
        "Caution": "Stretched",
        "Avoid": "Avoid",
    }
    return m.get(signal, "Balanced")


def _round_dict(d: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in d.items():
        try:
            out[k] = round(float(v), 4)
        except (TypeError, ValueError):
            pass
    return out
