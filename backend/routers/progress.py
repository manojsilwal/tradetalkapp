from fastapi import APIRouter
from .. import user_progress as up

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("")
def get_progress():
    """Return full user progress: XP, level, streak, badges."""
    return up.get_progress()


@router.post("/award")
def award_xp(action: str, note: str = ""):
    """Award XP for an action. Called internally by other endpoints."""
    return up.award_xp(action, note)


@router.get("/history")
def xp_history(limit: int = 20):
    return up.get_xp_history(limit)
