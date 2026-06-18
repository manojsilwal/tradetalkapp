"""UBDS v1.0 UI/UX benchmark — run from API and fetch latest report artifacts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_current_admin_user
from ..rate_limiter import rate_limit

router = APIRouter(
    prefix="/admin/ubds",
    tags=["ubds"],
    dependencies=[Depends(get_current_admin_user)],
)

_rl_eval = rate_limit("export")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class UbdsRunRequest(BaseModel):
    mode: Literal["fixture", "playwright"] = "fixture"


class UbdsRunResponse(BaseModel):
    ok: bool
    run_id: str
    status: str
    grade: str
    overall_score: float
    summary: dict[str, Any] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_text(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


@router.post("/run", response_model=UbdsRunResponse, dependencies=[Depends(_rl_eval)])
async def run_ubds_eval(body: UbdsRunRequest) -> UbdsRunResponse:
    """Execute UBDS benchmark and refresh dashboard + report files."""
    from backend.eval.ubds.runner import run_ubds

    repo = _repo_root()

    def _run():
        return run_ubds(mode=body.mode)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"UBDS run failed: {e}") from e

    files_path = repo / "evals" / "reports" / result.timestamp[:10]
    summary_path = files_path / "uiux_dashboard_summary.json"
    summary = _read_json(summary_path) or {}

    return UbdsRunResponse(
        ok=True,
        run_id=result.run_id,
        status=result.status,
        grade=result.grade,
        overall_score=result.overall_ui_behavior_design_score,
        summary=summary,
        files={
            "markdown": str(files_path / "uiux_behavior_design_report.md"),
            "json": str(files_path / "uiux_behavior_design_results.json"),
            "dashboard": str(summary_path),
        },
    )


@router.get("/summary")
async def get_ubds_summary() -> dict[str, Any]:
    repo = _repo_root()
    dash = repo / "frontend" / "public" / "dashboard" / "uiux-summary.json"
    data = _read_json(dash)
    if data:
        return data
    raise HTTPException(status_code=404, detail="No UBDS summary yet. Run a benchmark first.")


@router.get("/results")
async def get_ubds_results() -> dict[str, Any]:
    repo = _repo_root()
    latest = _read_json(repo / "evals" / "reports" / "latest_uiux_results.json")
    if latest:
        return latest
    raise HTTPException(status_code=404, detail="No UBDS results yet. Run a benchmark first.")


@router.get("/report")
async def get_ubds_report(
    format: str = Query("json", alias="format"),
) -> dict[str, Any]:
    repo = _repo_root()
    md = _read_text(repo / "evals" / "reports" / "latest_uiux.md")
    results = _read_json(repo / "evals" / "reports" / "latest_uiux_results.json") or {}
    if not md and not results:
        raise HTTPException(status_code=404, detail="No UBDS report yet. Run a benchmark first.")
    if format == "markdown":
        return {"markdown": md or ""}
    return {
        "markdown": md or "",
        "run_id": results.get("run_id"),
        "overall_score": results.get("overall_ui_behavior_design_score"),
        "grade": results.get("grade"),
        "status": results.get("status"),
        "scores": results.get("scores"),
        "task_results": results.get("task_results"),
    }


@router.get("/history")
async def get_ubds_history() -> list[dict[str, Any]]:
    repo = _repo_root()
    hist = _read_json(repo / "frontend" / "public" / "dashboard" / "uiux-history.json")
    return hist if isinstance(hist, list) else []
