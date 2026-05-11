"""Smoke evaluation over ``replay_corpus.json`` — no network (mock TimesFM path)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _corpus_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / "replay_corpus.json"


async def _run_one(ticker: str) -> str:
    from backend.predictor.agent import run_predictor_forecast

    out = await run_predictor_forecast(
        ticker,
        horizons=["5d"],
        tool_registry=None,
        emit_ledger=False,
    )
    return out.status


async def _run_batch(rows: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for row in rows:
        out.append(await _run_one(row["ticker"]))
    return out


def run_replay_smoke(*, limit: int = 12) -> Dict[str, Any]:
    path = _corpus_path()
    if not path.is_file():
        return {"ok": False, "error": f"missing {path}"}
    rows: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    statuses: List[str] = asyncio.run(_run_batch(rows[:limit]))
    ok_n = sum(1 for s in statuses if s == "ok")
    return {
        "ok": ok_n == len(statuses),
        "considered": len(statuses),
        "ok_count": ok_n,
        "sample_statuses": statuses[:5],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    lim = int(os.environ.get("PREDICTOR_EVAL_LIMIT", "12"))
    out = run_replay_smoke(limit=lim)
    logger.info("[predictor.eval] %s", out)
    raise SystemExit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
