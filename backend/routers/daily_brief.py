"""Daily Brief API — top gainers/losers with movement context and verdicts."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from ..daily_brief import build_daily_brief
from ..rate_limiter import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/daily-brief", tags=["daily-brief"])

_rl = rate_limit("default")


@router.get("")
@_rl
async def get_daily_brief(
    trade_date: Optional[str] = Query(
        None,
        description="ISO date YYYY-MM-DD; default = latest in BigQuery",
    ),
    losers: int = Query(20, ge=1, le=50),
    gainers: int = Query(10, ge=1, le=30),
) -> Dict[str, Any]:
    td: Optional[date] = None
    if trade_date:
        td = date.fromisoformat(trade_date)
    return build_daily_brief(trade_date=td, n_losers=losers, n_gainers=gainers)
