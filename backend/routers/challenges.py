"""Daily challenge routes."""

from __future__ import annotations

from typing import Any, Dict
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..auth import get_current_user, UserInfo
from .. import daily_challenge as dc
from .. import user_progress as up

router = APIRouter(prefix="/challenge", tags=["challenge"])


class AnswerRequest(BaseModel):
    """Request schema for submitting daily challenge answers."""
    answer: str


@router.get("/today")
def get_today(user: UserInfo = Depends(get_current_user)) -> Dict[str, Any]:
    """Retrieve today's active challenge for the user."""
    return dc.get_today_challenge(user.id)


@router.post("/answer")
def submit_answer(
    req: AnswerRequest,
    user: UserInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Submit an answer to the current daily challenge."""
    result = dc.submit_answer(user.id, req.answer)
    if result.get("resolved"):
        xp_result = up.award_xp(user.id, "daily_challenge")
        result["progress"] = xp_result
    return result


@router.get("/yesterday")
def yesterday_result(user: UserInfo = Depends(get_current_user)) -> Dict[str, Any]:
    """Retrieve the results of yesterday's daily challenge."""
    return dc.get_yesterday_result(user.id)

