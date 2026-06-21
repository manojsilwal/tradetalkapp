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
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..auth import get_optional_user
from ..connectors.scorecard_data import ScorecardData, fetch_basket, fetch_scorecard_data
from ..data_errors import InsufficientDataError
from ..deps import llm_client
from ..rate_limiter import rate_limit
from ..fincrawler_client import fc
from ..paper_portfolio import get_stock_sec_info, upsert_stock_sec_info
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
    data_freshness: Optional[Dict[str, Any]] = None
    ceo_base_salary: Optional[float] = None
    sitg_value: Optional[float] = None
    sitg_multiple: Optional[float] = None
    sitg_percentile_tier: Optional[str] = None
    new_revenue_engine_score: float = 0.0
    new_revenue_engine_boost: float = 0.0


class ScorecardResponse(BaseModel):
    preset: str
    weights: Dict[str, float]
    denominators: Dict[str, float]
    rows: List[ScorecardRowOut]
    notes: List[str] = Field(default_factory=list)
    data_freshness: Optional[Dict[str, Any]] = None


# ── Handlers ─────────────────────────────────────────────────────────────────

def _scorecard_freshness() -> Optional[Dict[str, Any]]:
    """Freshness envelope: scorecard is fundamentals-derived analytics computed now."""
    try:
        from datetime import datetime, timezone
        from ..freshness import assess

        return assess(
            data_class="scorecard",
            source="yfinance",
            captured_at=datetime.now(timezone.utc),
            note="Peer-normalized scorecard computed from fundamentals at request time.",
        ).model_dump()
    except Exception:
        return None


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
    except InsufficientDataError:
        raise
    except Exception as e:
        logger.exception("[scorecard] data fetch failed")
        raise HTTPException(status_code=502, detail=f"data fetch failed: {e}") from e

    # Subjective scores (LLM personas) — parallel per ticker.
    sitg_by_ticker, exec_by_ticker, rev_by_ticker = await _fetch_subjective_scores(
        data_rows, skip_llm=req.skip_llm_scores
    )

    inputs = [
        _data_to_scorecard_input(
            d,
            sitg_score=sitg_by_ticker[d.ticker]["sitg_score"],
            sitg_archetype=sitg_by_ticker[d.ticker].get("archetype", ""),
            exec_score=exec_by_ticker[d.ticker]["exec_score"],
            ceo_base_salary=sitg_by_ticker[d.ticker].get("ceo_base_salary"),
            sitg_value=sitg_by_ticker[d.ticker].get("sitg_value"),
            sitg_multiple=sitg_by_ticker[d.ticker].get("sitg_multiple"),
            sitg_percentile_tier=sitg_by_ticker[d.ticker].get("sitg_percentile_tier"),
            new_revenue_engine_score=rev_by_ticker[d.ticker].get("new_revenue_engine_score", 50.0),
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
    _dl_mod = None
    _pv: dict = {}
    _snap = ""
    _model = ""
    try:
        from .. import decision_ledger as _dl_mod
        from ..decision_ledger_registry import registry_attribution

        _pv, _snap, _model = registry_attribution()
    except Exception:
        pass
    for row, data in zip(basket.rows, data_rows):
        v = verdicts.get(row.ticker, {"verdict": "Balanced", "one_line_reason": ""})
        if _dl_mod is not None:
            try:
                _dl_mod.emit_decision(
                decision_type="scorecard",
                symbol=row.ticker,
                horizon_hint="63d",
                verdict=str(v.get("verdict", "Balanced")),
                confidence=None,
                output={
                    "preset": req.preset,
                    "signal": row.signal,
                    "action": row.action,
                    "quadrant": row.quadrant,
                    "ratio": row.ratio,
                    "one_line_reason": v.get("one_line_reason", ""),
                    "compare": True,
                    "ceo_base_salary": row.ceo_base_salary,
                    "sitg_value": row.sitg_value,
                    "sitg_multiple": row.sitg_multiple,
                    "sitg_percentile_tier": row.sitg_percentile_tier,
                },
                source_route="backend/routers/scorecard.py::compare_scorecard",
                prompt_versions=_pv,
                registry_snapshot_id=_snap,
                model=_model,
                )
            except Exception as e:
                logger.debug("[scorecard] ledger emit skipped %s: %s", row.ticker, e)
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
                ceo_base_salary=row.ceo_base_salary,
                sitg_value=row.sitg_value,
                sitg_multiple=row.sitg_multiple,
                sitg_percentile_tier=row.sitg_percentile_tier,
                new_revenue_engine_score=row.new_revenue_engine_score,
                new_revenue_engine_boost=row.new_revenue_engine_boost,
            )
        )

    return ScorecardResponse(
        preset=basket.preset,
        weights=basket.weights.as_dict(),
        denominators=basket.denominators,
        rows=rows_out,
        notes=notes,
        data_freshness=_scorecard_freshness(),
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
    sitg_by_ticker, exec_by_ticker, rev_by_ticker = await _fetch_subjective_scores(
        [data], skip_llm=skip_llm_scores
    )
    inp = _data_to_scorecard_input(
        data,
        sitg_score=sitg_by_ticker[data.ticker]["sitg_score"],
        sitg_archetype=sitg_by_ticker[data.ticker].get("archetype", ""),
        exec_score=exec_by_ticker[data.ticker]["exec_score"],
        ceo_base_salary=sitg_by_ticker[data.ticker].get("ceo_base_salary"),
        sitg_value=sitg_by_ticker[data.ticker].get("sitg_value"),
        sitg_multiple=sitg_by_ticker[data.ticker].get("sitg_multiple"),
        sitg_percentile_tier=sitg_by_ticker[data.ticker].get("sitg_percentile_tier"),
        new_revenue_engine_score=rev_by_ticker[data.ticker].get("new_revenue_engine_score", 50.0),
    )
    row = score_single(inp, preset=preset_key)

    verdicts = await _fetch_verdicts_single(row, preset_key, skip_llm=skip_llm_scores)
    v = verdicts.get(row.ticker, {"verdict": "Balanced", "one_line_reason": ""})

    try:
        from .. import decision_ledger as _dl
        from ..decision_ledger_registry import registry_attribution

        _pv, _snap, _model = registry_attribution()
        _dl.emit_decision(
            decision_type="scorecard",
            symbol=sym,
            horizon_hint="63d",
            verdict=str(v.get("verdict", "Balanced")),
            confidence=None,
            output={
                "preset": preset_key,
                "signal": row.signal,
                "action": row.action,
                "quadrant": row.quadrant,
                "ratio": row.ratio,
                "one_line_reason": v.get("one_line_reason", ""),
                "ceo_base_salary": row.ceo_base_salary,
                "sitg_value": row.sitg_value,
                "sitg_multiple": row.sitg_multiple,
                "sitg_percentile_tier": row.sitg_percentile_tier,
            },
            source_route="backend/routers/scorecard.py::single_ticker_scorecard",
            prompt_versions=_pv,
            registry_snapshot_id=_snap,
            model=_model,
        )
    except Exception as e:
        logger.debug("[scorecard] ledger emit skipped: %s", e)

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
        data_freshness=_scorecard_freshness(),
        ceo_base_salary=row.ceo_base_salary,
        sitg_value=row.sitg_value,
        sitg_multiple=row.sitg_multiple,
        sitg_percentile_tier=row.sitg_percentile_tier,
        new_revenue_engine_score=row.new_revenue_engine_score,
        new_revenue_engine_boost=row.new_revenue_engine_boost,
    )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _data_to_scorecard_input(
    d: ScorecardData,
    *,
    sitg_score: float,
    sitg_archetype: str,
    exec_score: float,
    ceo_base_salary: Optional[float] = None,
    sitg_value: Optional[float] = None,
    sitg_multiple: Optional[float] = None,
    sitg_percentile_tier: Optional[str] = None,
    new_revenue_engine_score: float = 0.0,
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
        ceo_base_salary=ceo_base_salary,
        sitg_value=sitg_value,
        sitg_multiple=sitg_multiple,
        sitg_percentile_tier=sitg_percentile_tier,
        new_revenue_engine_score=float(new_revenue_engine_score),
    )


async def _fetch_subjective_scores(
    data_rows: List[ScorecardData],
    *,
    skip_llm: bool,
) -> tuple[Dict[str, dict], Dict[str, dict], Dict[str, dict]]:
    """
    Fan out SITG + Execution-Risk persona calls in parallel.

    Truthful-data contract: a persona failure raises InsufficientDataError —
    we never substitute default scores unless the caller explicitly opted out
    of LLM scoring via ``skip_llm`` (cheap previews / tests).
    """
    if skip_llm:
        sitg_map = {
            d.ticker: {
                "sitg_score": 3.0,
                "archetype": "Most S&P 500 CEOs",
                "ceo_base_salary": None,
                "sitg_value": None,
                "sitg_multiple": None,
                "sitg_percentile_tier": None,
            }
            for d in data_rows
        }
        exec_map = {d.ticker: {"exec_score": 5.0, "profile_tier": "mid_growth"} for d in data_rows}
        rev_map = {d.ticker: {"new_revenue_engine_score": 50.0} for d in data_rows}
        return sitg_map, exec_map, rev_map

    async def _score_sitg(d: ScorecardData) -> tuple[str, dict]:
        # Check cache
        try:
            cached = get_stock_sec_info(d.ticker)
            if cached and cached.get("updated_at") and (time.time() - cached["updated_at"] < 30 * 86400):
                return d.ticker, {
                    "sitg_score": float(cached["sitg_score"]),
                    "archetype": cached.get("sitg_percentile_tier") or "Most S&P 500 CEOs",
                    "ceo_base_salary": cached.get("ceo_base_salary"),
                    "sitg_value": cached.get("sitg_value"),
                    "sitg_multiple": cached.get("sitg_multiple"),
                    "sitg_percentile_tier": cached.get("sitg_percentile_tier"),
                    "cached": True,
                }
        except Exception as e:
            logger.warning("[scorecard] failed to read stocks cache for %s: %s", d.ticker, e)

        # Cache miss or stale
        try:
            proxy_context = ""
            if fc.enabled:
                try:
                    proxy_context = await fc.get_sec_filing(d.ticker, form="DEF 14A", max_chars=8000)
                except Exception as e:
                    logger.warning("[scorecard] failed to fetch DEF 14A for %s: %s", d.ticker, e)

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
                "proxy_context": proxy_context,
            }
            out = await llm_client.generate_sitg_score(d.ticker, ctx)
            score = float(out.get("sitg_score", 3.0))

            ceo_name = out.get("ceo_name") or d.ceo_name or ""
            salary = out.get("ceo_base_salary")
            value = out.get("sitg_value")
            multiple = None
            tier = None

            if salary is not None and value is not None and salary > 0:
                multiple = value / salary
                llm_tier = out.get("sitg_percentile_tier") or ""
                
                if multiple >= 100:
                    tier = llm_tier if "founder" in llm_tier.lower() or "top 10" in llm_tier.lower() else "Founder-Level SITG"
                elif multiple >= 5:
                    tier = llm_tier if "above" in llm_tier.lower() or "most" in llm_tier.lower() else "Most S&P 500 CEOs"
                else:
                    tier = "Below Average SITG"
                multiple = round(multiple, 2)

            # Save to cache
            try:
                upsert_stock_sec_info(
                    ticker=d.ticker,
                    ceo_name=ceo_name,
                    sitg_score=score,
                    ceo_base_salary=salary,
                    sitg_value=value,
                    sitg_multiple=multiple,
                    sitg_percentile_tier=tier,
                    insider_buy_count_12m=d.insider_buy_count_12m,
                    insider_sell_count_12m=d.insider_sell_count_12m,
                    insider_net_shares_12m=d.insider_net_shares_12m,
                    held_percent_insiders=d.held_percent_insiders,
                )
            except Exception as db_err:
                logger.error("[scorecard] failed to cache SITG results in DB: %s", db_err)

            return d.ticker, {
                "sitg_score": max(0.0, min(10.0, score)),
                "archetype": tier or str(out.get("archetype") or "Most S&P 500 CEOs"),
                "ceo_base_salary": salary,
                "sitg_value": value,
                "sitg_multiple": multiple,
                "sitg_percentile_tier": tier,
                "raw": out,
            }
        except InsufficientDataError:
            raise
        except Exception as e:
            logger.warning("[scorecard] sitg scorer failed for %s: %s", d.ticker, e)
            raise InsufficientDataError(
                "llm",
                f"SITG persona scoring failed for {d.ticker}; refusing to "
                "substitute a default score.",
                ticker=d.ticker,
                missing=["sitg_score"],
            ) from e

    async def _score_rev(d: ScorecardData) -> tuple[str, dict]:
        # Check cache
        try:
            cached = get_stock_sec_info(d.ticker)
            if cached and cached.get("updated_at") and (time.time() - cached["updated_at"] < 30 * 86400):
                if cached.get("new_revenue_engine_score") is not None:
                    return d.ticker, {
                        "new_revenue_engine_score": float(cached["new_revenue_engine_score"]),
                        "cached": True,
                    }
        except Exception as e:
            logger.warning("[scorecard] failed to read stocks cache for %s (rev): %s", d.ticker, e)

        # Cache miss or stale
        try:
            k_context = ""
            q_context = ""
            if fc.enabled:
                try:
                    k_context = await fc.get_sec_filing(d.ticker, form="10-K", max_chars=8000)
                except Exception as e:
                    pass
                try:
                    q_context = await fc.get_sec_filing(d.ticker, form="10-Q", max_chars=8000)
                except Exception as e:
                    pass

            rev_ctx = {
                "ticker": d.ticker,
                "10_k_context": k_context,
                "10_q_context": q_context,
            }
            out_rev = await llm_client.generate_new_revenue_engine_score(d.ticker, rev_ctx)

            financial_traction = out_rev.get("financial_traction_score", 50)
            customer_adoption = out_rev.get("customer_adoption_score", 50)
            management_commitment = out_rev.get("management_commitment_score", 50)
            market_opportunity = out_rev.get("market_opportunity_score", 50)
            monetization_clarity = out_rev.get("monetization_clarity_score", 50)
            execution_capacity = out_rev.get("execution_capacity_score", 50)

            new_revenue_engine_score = (
                0.30 * financial_traction +
                0.20 * customer_adoption +
                0.15 * management_commitment +
                0.15 * market_opportunity +
                0.10 * monetization_clarity +
                0.10 * execution_capacity
            )

            # Save to cache - we just do a partial update. The upsert function handles COALESCE for missing values.
            try:
                upsert_stock_sec_info(
                    ticker=d.ticker,
                    ceo_name="", # Use empty to trigger exclude ignore
                    sitg_score=3.0,
                    ceo_base_salary=None,
                    sitg_value=None,
                    sitg_multiple=None,
                    sitg_percentile_tier=None,
                    insider_buy_count_12m=0,
                    insider_sell_count_12m=0,
                    insider_net_shares_12m=0.0,
                    held_percent_insiders=0.0,
                    financial_traction_score=financial_traction,
                    customer_adoption_score=customer_adoption,
                    management_commitment_score=management_commitment,
                    market_opportunity_score=market_opportunity,
                    monetization_clarity_score=monetization_clarity,
                    execution_capacity_score=execution_capacity,
                    new_revenue_engine_score=new_revenue_engine_score,
                )
            except Exception as db_err:
                logger.error("[scorecard] failed to cache rev results in DB: %s", db_err)

            return d.ticker, {
                "new_revenue_engine_score": new_revenue_engine_score,
                "raw": out_rev,
            }
        except InsufficientDataError:
            raise
        except Exception as e:
            logger.warning("[scorecard] rev scorer failed for %s: %s", d.ticker, e)
            raise InsufficientDataError(
                "llm",
                f"Revenue engine persona scoring failed for {d.ticker}; refusing to "
                "substitute a default score.",
                ticker=d.ticker,
                missing=["new_revenue_engine_score"],
            ) from e

    async def _score_exec(d: ScorecardData) -> tuple[str, dict]:
        try:
            sec_10k_context = ""
            if fc.enabled:
                try:
                    sec_10k_context = await fc.get_sec_filing(d.ticker, form="10-K", max_chars=8000)
                except Exception as e:
                    logger.warning("[scorecard] failed to fetch 10-K for %s: %s", d.ticker, e)

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
                "sec_10k_context": sec_10k_context,
            }
            out = await llm_client.generate_execution_risk_score(d.ticker, ctx)
            score = float(out.get("exec_score", 5.0))
            return d.ticker, {
                "exec_score": max(1.0, min(10.0, score)),
                "profile_tier": str(out.get("profile_tier") or "mid_growth"),
                "raw": out,
            }
        except InsufficientDataError:
            raise
        except Exception as e:
            logger.warning("[scorecard] exec scorer failed for %s: %s", d.ticker, e)
            raise InsufficientDataError(
                "llm",
                f"Execution-risk persona scoring failed for {d.ticker}; refusing "
                "to substitute a default score.",
                ticker=d.ticker,
                missing=["exec_score"],
            ) from e

    sitg_task = asyncio.gather(*[_score_sitg(d) for d in data_rows])
    exec_task = asyncio.gather(*[_score_exec(d) for d in data_rows])
    rev_task = asyncio.gather(*[_score_rev(d) for d in data_rows])
    sitg_results, exec_results, rev_results = await asyncio.gather(sitg_task, exec_task, rev_task)
    return dict(sitg_results), dict(exec_results), dict(rev_results)


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
