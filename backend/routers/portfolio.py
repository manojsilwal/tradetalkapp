import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import get_current_user_or_dev, UserInfo
from ..cron_auth import require_cron_secret
from ..gemini_llm import gemini_extract_holdings_from_image, resolve_gemini_api_key
from .. import paper_portfolio as pp
from .. import user_progress as up
from ..portfolio_holdings_reconcile import (
    aggregate_open_long_positions,
    holdings_dicts_from_model_json,
    normalize_extracted_holdings,
    reconcile_holdings,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 10


class AddPositionRequest(BaseModel):
    ticker: str
    direction: str = "LONG"
    allocated: Optional[float] = None
    price: Optional[float] = None
    shares: Optional[float] = None
    source: str = "manual"
    note: str = ""


class HoldingsRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str
    shares: Optional[float] = None
    avg_cost: Optional[float] = None


class PreviewHoldingsRequest(BaseModel):
    items: List[HoldingsRow]
    full_snapshot: bool = False


class ApplyHoldingsRequest(BaseModel):
    items: List[HoldingsRow]
    full_snapshot: bool = False
    source: str = Field(default="holdings_import", max_length=64)
    note: str = Field(default="", max_length=2000)


class UserActionLogRequest(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=64)
    entity_type: Optional[str] = Field(default=None, max_length=64)
    entity_id: Optional[str] = Field(default=None, max_length=128)
    symbol: Optional[str] = Field(default=None, max_length=16)
    page: Optional[str] = Field(default=None, max_length=64)
    metadata: Optional[dict] = None

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().upper() if v else v


def _reconcile_payload(user_id: str, raw_items: list, full_snapshot: bool) -> dict:
    normalized = normalize_extracted_holdings([r.model_dump() for r in raw_items])
    current = aggregate_open_long_positions(pp.get_positions(user_id, include_closed=False))
    return {
        "holdings": normalized,
        "reconciliation": reconcile_holdings(normalized, current, full_snapshot=full_snapshot),
        "current_open_tickers": sorted(current.keys()),
    }


def _holdings_rows_from_model_json(text: str) -> List[HoldingsRow]:
    rows: List[HoldingsRow] = []
    for x in holdings_dicts_from_model_json(text):
        try:
            rows.append(HoldingsRow.model_validate(x))
        except Exception:
            continue
    return rows


async def _parse_one_upload(file: UploadFile) -> tuple[List[HoldingsRow], Optional[str]]:
    """Returns (rows, error_message). error_message is set when this image failed."""
    raw = await file.read()
    if len(raw) > MAX_IMAGE_BYTES:
        return [], f"{file.filename or 'image'}: too large (max 4MB)"
    mime = file.content_type or "image/jpeg"
    try:
        text = gemini_extract_holdings_from_image(image_bytes=raw, mime_type=mime)
        return _holdings_rows_from_model_json(text), None
    except json.JSONDecodeError:
        return [], f"{file.filename or 'image'}: model returned invalid JSON"
    except Exception as e:
        return [], f"{file.filename or 'image'}: {str(e)[:200]}"


@router.post("/position")
def add_position(req: AddPositionRequest, user: UserInfo = Depends(get_current_user_or_dev)):
    result = pp.add_position(
        user_id=user.id,
        ticker=req.ticker,
        direction=req.direction,
        allocated=req.allocated,
        source=req.source,
        note=req.note,
        price=req.price,
        shares=req.shares,
    )
    up.award_xp(user.id, "prediction_log", note=req.ticker)
    return result


@router.get("/morning-brief")
def get_morning_brief(user: UserInfo = Depends(get_current_user_or_dev)):
    """Personalized Your Morning brief for the authenticated user's portfolio."""
    from ..morning_brief import build_morning_brief

    return build_morning_brief(user.id)


@router.get("/timeline")
def get_portfolio_timeline(
    limit: int = 20,
    user: UserInfo = Depends(get_current_user_or_dev),
):
    """Recent portfolio memory timeline (events + reaction memory)."""
    from ..portfolio_timeline import build_timeline

    return {"items": build_timeline(user.id, limit=limit)}


@router.post("/user-actions/log")
def log_user_action_route(
    body: UserActionLogRequest,
    user: UserInfo = Depends(get_current_user_or_dev),
):
    """Log implicit behavioural signal (non-blocking for callers)."""
    from .. import portfolio_memory as pm
    from .. import user_preferences as uprefs

    action_id = pm.log_user_action(
        user.id,
        body.action_type,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        symbol=body.symbol,
        page=body.page,
        metadata=body.metadata,
    )
    # Dual-write favourites for chat personalization
    try:
        ctx = {"ticker": body.symbol} if body.symbol else {}
        uprefs.learn_from_action(user.id, body.action_type, ctx)
    except Exception:
        pass
    return {"ok": True, "action_id": action_id}


@router.get("/positions")
def get_positions(include_closed: bool = False, user: UserInfo = Depends(get_current_user_or_dev)):
    return pp.get_positions(user.id, include_closed)


@router.get("/performance")
def get_performance(user: UserInfo = Depends(get_current_user_or_dev)):
    perf = pp.get_portfolio_performance(user.id)
    if perf.get("beating_spy"):
        up.award_xp(user.id, "prediction_right", note="beat_spy")
    return perf


@router.post("/close/{position_id}")
def close_position(position_id: str, user: UserInfo = Depends(get_current_user_or_dev)):
    return pp.close_position(user.id, position_id)


@router.post("/preview-holdings-import")
def preview_holdings_import(body: PreviewHoldingsRequest, user: UserInfo = Depends(get_current_user_or_dev)):
    return _reconcile_payload(user.id, body.items, body.full_snapshot)


@router.post("/parse-holdings-image")
async def parse_holdings_image(
    full_snapshot: str = Form("false"),
    file: Optional[UploadFile] = File(None),
    files: Optional[List[UploadFile]] = File(None),
    user: UserInfo = Depends(get_current_user_or_dev),
):
    if not resolve_gemini_api_key():
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY (or GOOGLE_API_KEY) is not configured for screenshot import",
        )
    uploads: List[UploadFile] = []
    if file is not None:
        uploads.append(file)
    uploads.extend(files or [])
    if not uploads:
        raise HTTPException(status_code=400, detail="Upload at least one image")
    if len(uploads) > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"Too many images (max {MAX_IMAGES_PER_REQUEST})",
        )

    fs_flag = str(full_snapshot).lower() in ("1", "true", "yes", "on")
    merged: List[HoldingsRow] = []
    parse_errors: List[str] = []
    images_ok = 0

    for upload in uploads:
        rows, err = await _parse_one_upload(upload)
        if err:
            parse_errors.append(err)
            logger.warning("parse-holdings-image: %s", err)
            continue
        images_ok += 1
        merged.extend(rows)

    if images_ok == 0:
        detail = parse_errors[0] if len(parse_errors) == 1 else "; ".join(parse_errors[:5])
        raise HTTPException(status_code=502, detail=detail or "Could not parse any image")

    payload = _reconcile_payload(user.id, merged, fs_flag)
    payload["images_parsed"] = images_ok
    payload["images_failed"] = len(parse_errors)
    if parse_errors:
        payload["parse_warnings"] = parse_errors
    return payload


@router.post("/snapshots/cron", dependencies=[Depends(require_cron_secret)])
async def run_portfolio_snapshots_cron():
    """Nightly portfolio snapshot job (cron / GitHub Actions). Idempotent per user+date."""
    from ..portfolio_snapshots_job import run_portfolio_snapshots_job

    return await run_portfolio_snapshots_job()


@router.post("/apply-holdings-import")
def apply_holdings_import_route(body: ApplyHoldingsRequest, user: UserInfo = Depends(get_current_user_or_dev)):
    result = pp.apply_holdings_import(
        user.id,
        [r.model_dump() for r in body.items],
        full_snapshot=body.full_snapshot,
        source=body.source,
        note=body.note,
    )
    if result.get("applied"):
        up.award_xp(user.id, "prediction_log", note="holdings_import")
    return result
