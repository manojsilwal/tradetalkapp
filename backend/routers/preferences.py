"""User preferences API — GET/PUT endpoints for explicit preference management."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import UserInfo, get_optional_user
from .. import user_preferences as uprefs

router = APIRouter(prefix="/preferences", tags=["preferences"])


class PreferencesUpdateRequest(BaseModel):
    """Partial update — only include fields you want to change."""
    risk_tolerance: Optional[str] = None       # conservative | moderate | aggressive
    investment_horizon: Optional[str] = None    # short | medium | long
    explain_style: Optional[str] = None         # simple | balanced | technical
    watchlist: Optional[list] = None            # explicit ticker watchlist


@router.get("")
async def get_user_preferences(
    _user: Optional[UserInfo] = Depends(get_optional_user),
):
    """Return current user preferences (learned + explicit)."""
    if not _user:
        return {"authenticated": False, "preferences": uprefs.DEFAULT_PREFERENCES}
    prefs = uprefs.get_preferences(_user.id)
    signals = uprefs.get_signals(_user.id)
    return {
        "authenticated": True,
        "user_id": _user.id,
        "preferences": prefs,
        "signal_counts": {
            "tickers_tracked": len(signals.get("ticker_counts", {})),
            "tools_tracked": len(signals.get("tool_counts", {})),
        },
    }


@router.put("")
async def update_user_preferences(
    body: PreferencesUpdateRequest,
    _user: Optional[UserInfo] = Depends(get_optional_user),
):
    """Explicitly update user preferences."""
    if not _user:
        raise HTTPException(status_code=401, detail="Authentication required to save preferences")

    updates = {}
    if body.risk_tolerance and body.risk_tolerance in ("conservative", "moderate", "aggressive"):
        updates["risk_tolerance"] = body.risk_tolerance
    if body.investment_horizon and body.investment_horizon in ("short", "medium", "long"):
        updates["investment_horizon"] = body.investment_horizon
    if body.explain_style and body.explain_style in ("simple", "balanced", "technical"):
        updates["explain_style"] = body.explain_style
    if body.watchlist is not None:
        updates["watchlist"] = [t.upper().strip() for t in body.watchlist[:20] if t.strip()]

    if not updates:
        raise HTTPException(status_code=422, detail="No valid preference fields provided")

    prefs = uprefs.update_preferences(_user.id, updates)
    return {"updated": list(updates.keys()), "preferences": prefs}
