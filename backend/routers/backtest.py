"""Backtest and strategy endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..schemas import BacktestResult
from ..auth import get_optional_user
from ..agent_policy_guardrails import ensure_capability, redact_secrets_in_text
from ..rate_limiter import rate_limit
from ..deps import knowledge_store, llm_client, up
from ..strategy_validator import validate_strategy
from .. import user_preferences as uprefs

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
        rules = await parse_strategy(strat, req.start_date, req.end_date, llm_client, knowledge_store)
    else:
        raise HTTPException(status_code=400, detail="Provide either preset_id or non-empty strategy text.")

    result = await run_backtest(rules, llm_client, knowledge_store)
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

    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "backtest", note=(req.preset_id or req.strategy)[:40])
            uprefs.learn_from_action(_auth_user.id, "backtest", {
                "ticker": ",".join(rules.universe[:3]) if hasattr(rules, "universe") else "",
            })
        except Exception:
            pass

    return result


@router.get("/strategies/leaderboard")
async def strategy_leaderboard(n: int = 20):
    """Return top N backtested strategies sorted by CAGR."""
    entries = knowledge_store.get_strategy_leaderboard(n=n)
    return {"strategies": entries, "total": len(entries)}
