"""SwarmScore evaluator — run from API and fetch latest report artifacts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..rate_limiter import rate_limit

router = APIRouter(prefix="/admin/swarm-score", tags=["swarm-score"])

_rl_eval = rate_limit("export")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class SwarmScoreRunRequest(BaseModel):
    mode: Literal["fixture", "dry-run"] = "fixture"


class SwarmScoreRunResponse(BaseModel):
    ok: bool
    run_id: str
    decision: str
    winner: str
    production_aes: Optional[float] = None
    winning_aes: Optional[float] = None
    score_delta: Optional[float] = None
    swarm_advantage: Optional[float] = None
    complexity_tax: str = ""
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


@router.post("/run", response_model=SwarmScoreRunResponse, dependencies=[Depends(_rl_eval)])
async def run_swarm_score_eval(body: SwarmScoreRunRequest) -> SwarmScoreRunResponse:
    """Execute the full SwarmScore evaluator and refresh dashboard + report files."""
    from backend.eval.swarm_score.runner import run_swarm_score

    repo = _repo_root()

    def _run() -> dict[str, Any]:
        return run_swarm_score(repo_root=repo, mode=body.mode, write_outputs=True)

    try:
        output = await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SwarmScore run failed: {e}") from e

    summary = output.get("result", {}).get("dashboard_notification") or {}
    if not summary:
        dash_path = repo / "frontend" / "public" / "dashboard" / "eval-summary.json"
        summary = _read_json(dash_path) or {}

    return SwarmScoreRunResponse(
        ok=True,
        run_id=str(output.get("run_id") or summary.get("run_id") or ""),
        decision=str(output.get("decision") or ""),
        winner=str(output.get("winner") or ""),
        production_aes=output.get("production_aes"),
        winning_aes=output.get("winning_aes"),
        score_delta=output.get("delta"),
        swarm_advantage=output.get("swarm_advantage"),
        complexity_tax=str(output.get("complexity_tax") or ""),
        summary=summary,
        files={k: str(v) for k, v in (output.get("files") or {}).items()},
    )


@router.get("/summary")
async def get_swarm_score_summary() -> dict[str, Any]:
    """Latest dashboard summary JSON (from last run)."""
    repo = _repo_root()
    dash = repo / "frontend" / "public" / "dashboard" / "eval-summary.json"
    data = _read_json(dash)
    if data:
        return data
    latest = _read_json(repo / "evals" / "reports" / "latest_results.json")
    if latest and latest.get("dashboard_notification"):
        return latest["dashboard_notification"]
    raise HTTPException(status_code=404, detail="No SwarmScore summary yet. Run an evaluation first.")


@router.get("/results")
async def get_swarm_score_results() -> dict[str, Any]:
    """Full structured results JSON from the latest run."""
    repo = _repo_root()
    latest = _read_json(repo / "evals" / "reports" / "latest_results.json")
    if latest:
        return latest
    raise HTTPException(status_code=404, detail="No SwarmScore results yet. Run an evaluation first.")


@router.get("/report")
async def get_swarm_score_report(
    format: Literal["markdown", "json"] = Query("markdown", alias="format"),
) -> dict[str, Any]:
    """Weekly effectiveness report — markdown body or metadata wrapper."""
    repo = _repo_root()
    md_path = repo / "evals" / "reports" / "latest.md"
    md = _read_text(md_path)
    if not md:
        dated_dirs = sorted((repo / "evals" / "reports").glob("*/weekly_swarm_effectiveness_report.md"))
        if dated_dirs:
            md = _read_text(dated_dirs[-1])
    if not md:
        raise HTTPException(status_code=404, detail="No report found. Run an evaluation first.")

    results = _read_json(repo / "evals" / "reports" / "latest_results.json") or {}
    if format == "json":
        return {"markdown": md, "results": results}
    return {"markdown": md, "run_id": results.get("run_id"), "decision": results.get("decision")}


@router.get("/history")
async def get_swarm_score_history() -> list[dict[str, Any]]:
    """Compact history entries written to dashboard eval-history.json."""
    repo = _repo_root()
    hist_path = repo / "frontend" / "public" / "dashboard" / "eval-history.json"
    if not hist_path.is_file():
        return []
    try:
        data = json.loads(hist_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []
