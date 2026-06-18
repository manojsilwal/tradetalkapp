"""User XP and progress routes."""

from __future__ import annotations

from typing import Any, Dict, List
from fastapi import APIRouter, Depends
from ..auth import get_current_user, UserInfo
from .. import user_progress as up

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("")
def get_progress(user: UserInfo = Depends(get_current_user)) -> Dict[str, Any]:
    """Retrieve the current progress and XP details for the logged-in user."""
    return up.get_progress(user.id)


@router.post("/award")
def award_xp(
    action: str,
    note: str = "",
    user: UserInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Award XP to the user for completing a specific action."""
    return up.award_xp(user.id, action, note)


@router.get("/history")
def xp_history(
    limit: int = 20,
    user: UserInfo = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """Retrieve the recent XP transactions/history for the user."""
    return up.get_xp_history(user.id, limit)

