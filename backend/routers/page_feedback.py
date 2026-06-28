"""Per-page user feedback API."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..auth import UserInfo, get_current_admin_user, get_optional_user
from .. import page_feedback as pf

router = APIRouter(prefix="/page-feedback", tags=["page-feedback"])


class PageFeedbackRequest(BaseModel):
    page: str = Field(..., min_length=1, max_length=256)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=2000)
    symbol: Optional[str] = Field(default=None, max_length=16)

    @field_validator("comment")
    @classmethod
    def strip_comment(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        return s or None


class PageFeedbackResponse(BaseModel):
    ok: bool = True
    id: str


class PageFeedbackSummaryResponse(BaseModel):
    pages: List[Dict[str, Any]]


@router.post("", response_model=PageFeedbackResponse)
async def submit_page_feedback(
    body: PageFeedbackRequest,
    user: Optional[UserInfo] = Depends(get_optional_user),
):
    """Record anonymous or authenticated page feedback."""
    if body.rating is None and not body.comment:
        raise HTTPException(status_code=422, detail="rating or comment is required")
    try:
        fid = pf.save_feedback(
            user_id=user.id if user else None,
            page=body.page,
            rating=body.rating,
            comment=body.comment,
            symbol=body.symbol,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save feedback") from exc
    return PageFeedbackResponse(id=fid)


@router.get("/summary", response_model=PageFeedbackSummaryResponse)
async def page_feedback_summary(
    _admin: UserInfo = Depends(get_current_admin_user),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Admin-only aggregate feedback by page."""
    return PageFeedbackSummaryResponse(pages=pf.feedback_summary(limit_pages=limit))
