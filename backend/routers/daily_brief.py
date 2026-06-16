"""Daily Brief API — top gainers/losers with movement context and verdicts."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from ..daily_brief import (
    build_daily_brief,
    compute_data_freshness,
    expected_last_session,
    get_deep_refresh_status,
    get_latest_trade_date,
    materialize_heuristic_snapshot,
    overlay_realtime_quotes,
    run_deep_refresh,
)
from ..deps import llm_client
from ..rate_limiter import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/daily-brief", tags=["daily-brief"])

_rl = rate_limit("default")
_rl_expensive = rate_limit("expensive")


async def _deep_refresh_task(
    trade_date: Optional[date],
    losers: int,
    gainers: int,
) -> None:
    try:
        await run_deep_refresh(
            trade_date=trade_date,
            n_losers=losers,
            n_gainers=gainers,
            llm_client=llm_client,
        )
    except Exception as e:
        logger.warning("[DailyBrief] background deep refresh failed: %s", e)


@router.get("", dependencies=[Depends(_rl)])
async def get_daily_brief(
    trade_date: Optional[str] = Query(
        None,
        description="ISO date YYYY-MM-DD; default = latest in BigQuery",
    ),
    losers: int = Query(20, ge=1, le=50),
    gainers: int = Query(10, ge=1, le=30),
    refresh: bool = Query(
        False,
        description="If true, recompute movers instead of reading snapshot",
    ),
) -> Dict[str, Any]:
    td: Optional[date] = None
    if trade_date:
        td = date.fromisoformat(trade_date)
    payload = build_daily_brief(
        trade_date=td,
        n_losers=losers,
        n_gainers=gainers,
        use_snapshot=not refresh,
        persist=False,
    )
    payload = overlay_realtime_quotes(payload, force=True)
    # A successful realtime overlay means prices were refreshed live.
    if payload.get("realtime_overlay"):
        from datetime import datetime, timezone
        from ..freshness import assess_home_live

        now = datetime.now(timezone.utc)
        payload["data_freshness"] = assess_home_live(
            source="realtime_overlay",
            as_of=expected_last_session().isoformat(),
            captured_at=now,
        ).model_dump(mode="json")
    elif not payload.get("data_freshness"):
        payload["data_freshness"] = compute_data_freshness(
            get_latest_trade_date(), source="snapshot"
        )
    from ..morning_brief import _market_session_context
    payload["market_session"] = _market_session_context()
    payload["deep_refresh"] = get_deep_refresh_status()
    return payload


@router.post("/deep-refresh", dependencies=[Depends(_rl_expensive)])
async def post_deep_refresh(
    background_tasks: BackgroundTasks,
    trade_date: Optional[str] = Query(None),
    losers: int = Query(20, ge=1, le=50),
    gainers: int = Query(10, ge=1, le=30),
    wait: bool = Query(
        False,
        description="If true, block until deep refresh completes (slow)",
    ),
) -> Dict[str, Any]:
    td = date.fromisoformat(trade_date) if trade_date else None
    status = get_deep_refresh_status()
    if status.get("status") == "running":
        return {"accepted": False, "deep_refresh": status}

    if wait:
        payload = await run_deep_refresh(
            trade_date=td,
            n_losers=losers,
            n_gainers=gainers,
            llm_client=llm_client,
        )
        return {"accepted": True, "completed": True, **payload}

    background_tasks.add_task(_deep_refresh_task, td, losers, gainers)
    return {
        "accepted": True,
        "completed": False,
        "message": "Deep refresh started in background",
        "deep_refresh": get_deep_refresh_status(),
    }


@router.get("/deep-refresh/status", dependencies=[Depends(_rl)])
async def get_deep_refresh_status_route() -> Dict[str, Any]:
    return get_deep_refresh_status()


@router.get("/screener", dependencies=[Depends(_rl)])
async def get_screener_results(
    trade_date: Optional[str] = Query(
        None,
        description="ISO date YYYY-MM-DD; default = latest in database",
    ),
) -> Dict[str, Any]:
    td: Optional[date] = None
    if trade_date:
        td = date.fromisoformat(trade_date)
    else:
        from ..daily_brief import get_latest_trade_date
        td = get_latest_trade_date() or date.today()

    from ..daily_brief import _adjust_weekend_to_friday
    if td:
        td = _adjust_weekend_to_friday(td)
        
    from ..daily_brief import load_snapshot
    snapshot = load_snapshot(td)
    if not snapshot or not snapshot.get("rows"):
        return {
            "trade_date": td.isoformat() if isinstance(td, date) else str(td),
            "source": "none",
            "verdict_tier": "deep",
            "rows": [],
            "message": "No pre-scored daily snapshot found for this session.",
        }

    rows = snapshot["rows"]
    buy_sell_signals = [
        r for r in rows
        if r.get("verdict") in ("Strong Buy", "Buy", "Sell")
    ]
    
    result = {
        "trade_date": snapshot.get("trade_date"),
        "source": snapshot.get("source"),
        "verdict_tier": snapshot.get("verdict_tier"),
        "updated_at": snapshot.get("updated_at"),
        "rows": buy_sell_signals,
    }
    return overlay_realtime_quotes(result)


@router.post("/materialize", dependencies=[Depends(_rl)])
async def post_materialize_heuristic(
    trade_date: Optional[str] = Query(None),
    losers: int = Query(20, ge=1, le=50),
    gainers: int = Query(10, ge=1, le=30),
) -> Dict[str, Any]:
    """Manually persist heuristic snapshot (same as cron step)."""
    td = date.fromisoformat(trade_date) if trade_date else None
    return materialize_heuristic_snapshot(td, losers, gainers)
