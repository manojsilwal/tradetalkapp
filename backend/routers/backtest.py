"""Backtest and strategy endpoints."""
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..schemas import BacktestResult
from ..auth import get_optional_user
from ..agent_policy_guardrails import ensure_capability, redact_secrets_in_text
from ..rate_limiter import rate_limit
from ..deps import knowledge_store, llm_client, up
from ..strategy_validator import validate_strategy
from ..telemetry import get_request_id
from .. import user_preferences as uprefs

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backtest"])

_rl_expensive = rate_limit("expensive")


class BacktestRequest(BaseModel):
    """Either preset_id (built-in) or strategy (plain English)."""
    strategy: str = ""
    preset_id: Optional[str] = None
    start_date: str = "2020-01-01"
    end_date: str = "2024-01-01"


@router.get("/strategies/presets")
async def list_strategy_presets():
    """Catalog of code-defined strategies."""
    from ..strategy_presets import list_preset_summaries
    return {"presets": list_preset_summaries()}


@router.post("/backtest/validate")
async def validate_backtest_request(req: BacktestRequest):
    """Pre-flight validation — check if strategy is meaningful before running."""
    result = validate_strategy(req.strategy, req.start_date, req.end_date, req.preset_id or "")
    return result


@router.post("/backtest", response_model=BacktestResult, dependencies=[Depends(_rl_expensive)])
async def run_backtest_endpoint(req: BacktestRequest, _auth_user=Depends(get_optional_user)):
    """Run a backtest from a preset_id or plain-English strategy text."""
    from ..strategy_parser import parse_strategy
    from ..backtest_engine import run_backtest
    from ..strategy_presets import get_preset_rules

    rid = get_request_id() or "unknown"
    t0 = time.monotonic()

    pid = (req.preset_id or "").strip()
    strat = (req.strategy or "").strip()

    # Pre-flight validation for custom strategies
    if strat and not pid:
        validation = validate_strategy(strat, req.start_date, req.end_date)
        if not validation.get("valid"):
            raise HTTPException(status_code=422, detail=validation)

    if pid:
        try:
            rules = get_preset_rules(pid, req.start_date, req.end_date)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    elif strat:
        try:
            rules = await parse_strategy(strat, req.start_date, req.end_date, llm_client, knowledge_store)
        except Exception as e:
            logger.exception("[backtest] parse_strategy failed req_id=%s", rid)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": redact_secrets_in_text(str(e)),
                    "request_id": rid,
                    "stage": "parse_strategy",
                },
            ) from e
    else:
        raise HTTPException(status_code=400, detail="Provide either preset_id or non-empty strategy text.")

    try:
        result = await run_backtest(rules, llm_client, knowledge_store)
    except Exception as e:
        logger.exception("[backtest] run_backtest failed req_id=%s", rid)
        raise HTTPException(
            status_code=500,
            detail={
                "error": redact_secrets_in_text(str(e)),
                "request_id": rid,
                "stage": "run_backtest",
            },
        ) from e

    elapsed = time.monotonic() - t0
    logger.info(
        "[backtest] ok req_id=%s duration_s=%.2f preset=%s rag_docs=%s",
        rid,
        elapsed,
        pid or "(custom)",
        result.retrieval_telemetry.retrieved_docs_count,
    )
    print(
        f"[BacktestRAG] retrieved_docs_count={result.retrieval_telemetry.retrieved_docs_count} "
        f"reflection_hits={result.retrieval_telemetry.reflection_hits}"
    )

    try:
        ensure_capability("backtest", "knowledge_write")
        knowledge_store.add_backtest(result)
        knowledge_store.add_reflection(result)
    except Exception as e:
        print(f"[KnowledgeHook] add_backtest failed: {redact_secrets_in_text(str(e))}")

    _emit_backtest_decision(pid, strat, result)

    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "backtest", note=(req.preset_id or req.strategy)[:40])
            uprefs.learn_from_action(_auth_user.id, "backtest", {
                "ticker": ",".join(rules.universe[:3]) if hasattr(rules, "universe") else "",
            })
        except Exception:
            pass

    return result


def _emit_backtest_decision(preset_id: str, strategy_text: str, result: BacktestResult) -> None:
    """Decision-Outcome Ledger emit for the backtest explainer verdict.

    Phase F capture contract — the AI explanation is a user-facing verdict
    surface; horizon ``none`` because the backtest grades against its own
    historical window, not a forward market outcome. Never raises.
    """
    try:
        from .. import decision_ledger as _dl
        from ..decision_ledger_registry import registry_attribution

        universe = list(getattr(result.strategy, "universe", []) or [])
        features = [
            _dl.FeatureValue(name="cagr", value_num=float(result.cagr)),
            _dl.FeatureValue(name="sharpe_ratio", value_num=float(result.sharpe_ratio)),
            _dl.FeatureValue(name="max_drawdown", value_num=float(result.max_drawdown)),
            _dl.FeatureValue(name="win_rate", value_num=float(result.win_rate)),
            _dl.FeatureValue(name="total_trades", value_num=float(result.total_trades)),
            _dl.FeatureValue(name="benchmark_cagr", value_num=float(result.benchmark_cagr)),
            _dl.FeatureValue(name="outperformed", value_str=str(bool(result.outperformed))),
        ]
        pv, snap, model = registry_attribution(
            roles=["backtest_explainer"] + (["strategy_parser"] if strategy_text and not preset_id else [])
        )
        _dl.emit_decision(
            decision_type="backtest_verdict",
            symbol=(universe[0] if len(universe) == 1 else ""),
            horizon_hint="none",
            verdict="OUTPERFORMED" if result.outperformed else "UNDERPERFORMED",
            output={
                "preset_id": preset_id or "",
                "strategy_text": (strategy_text or "")[:500],
                "strategy_name": getattr(result.strategy, "name", "") or "",
                "universe": universe[:10],
                "ai_explanation": (result.ai_explanation or "")[:2000],
                "total_return_pct": float(result.total_return_pct),
            },
            source_route="backend/routers/backtest.py::run_backtest_endpoint",
            features=features,
            prompt_versions=pv,
            registry_snapshot_id=snap,
            model=model,
        )
    except Exception as e:
        logger.debug("[backtest] ledger emit skipped: %s", e)


@router.get("/strategies/leaderboard")
async def strategy_leaderboard(n: int = 20):
    """Return top N backtested strategies sorted by CAGR."""
    entries = knowledge_store.get_strategy_leaderboard(n=n)
    return {"strategies": entries, "total": len(entries)}
