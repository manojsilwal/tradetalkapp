from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..auth import get_current_user, UserInfo
from .. import paper_portfolio as pp
from .. import user_progress as up

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class AddPositionRequest(BaseModel):
    ticker:    str
    direction: str   = "LONG"
    allocated: float = 1000.0
    source:    str   = "manual"
    note:      str   = ""


@router.post("/position")
def add_position(req: AddPositionRequest, user: UserInfo = Depends(get_current_user)):
    result = pp.add_position(
        user_id=user.id, ticker=req.ticker, direction=req.direction,
        allocated=req.allocated, source=req.source, note=req.note,
    )
    up.award_xp(user.id, "prediction_log", note=req.ticker)
    return result


@router.get("/positions")
def get_positions(include_closed: bool = False, user: UserInfo = Depends(get_current_user)):
    return pp.get_positions(user.id, include_closed)


@router.get("/performance")
def get_performance(user: UserInfo = Depends(get_current_user)):
    perf = pp.get_portfolio_performance(user.id)
    if perf.get("beating_spy"):
        up.award_xp(user.id, "prediction_right", note="beat_spy")
    return perf


@router.post("/close/{position_id}")
def close_position(position_id: str, user: UserInfo = Depends(get_current_user)):
    return pp.close_position(user.id, position_id)
