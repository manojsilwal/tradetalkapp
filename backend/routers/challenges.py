from fastapi import APIRouter
from pydantic import BaseModel
from .. import daily_challenge as dc
from .. import user_progress as up

router = APIRouter(prefix="/challenge", tags=["challenge"])


class AnswerRequest(BaseModel):
    answer: str


@router.get("/today")
def get_today():
    """Return today's challenge (without the correct answer)."""
    return dc.get_today_challenge()


@router.post("/answer")
def submit_answer(req: AnswerRequest):
    """Submit the user's answer. Awards XP immediately for quiz; pending for others."""
    result = dc.submit_answer(req.answer)
    if result.get("resolved"):
        xp_result = up.award_xp("daily_challenge")
        result["progress"] = xp_result
    return result


@router.get("/yesterday")
def yesterday_result():
    """Return yesterday's challenge resolution (for Type A/B challenges)."""
    return dc.get_yesterday_result()
