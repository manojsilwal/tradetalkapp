from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from .. import paper_portfolio as pp
from .. import user_progress as up

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class AddPositionRequest(BaseModel):
    ticker:    str
    direction: str = "LONG"
    allocated: float = 1000.0
    source:    str   = "manual"
    note:      str   = ""


@router.post("/position")
def add_position(req: AddPositionRequest):
    result = pp.add_position(
        ticker=req.ticker, direction=req.direction,
        allocated=req.allocated, source=req.source, note=req.note,
    )
    up.award_xp("prediction_log", note=req.ticker)
    return result


@router.get("/positions")
def get_positions(include_closed: bool = False):
    return pp.get_positions(include_closed)


@router.get("/performance")
def get_performance():
    perf = pp.get_portfolio_performance()
    # Award XP if beating SPY and hasn't been awarded today
    if perf.get("beating_spy"):
        up.award_xp("prediction_right", note="beat_spy")
    return perf


@router.post("/close/{position_id}")
def close_position(position_id: str):
    return pp.close_position(position_id)
