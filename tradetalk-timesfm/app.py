"""
TimesFM HTTP service — thin FastAPI wrapper.

Production installs ``timesfm[torch,xreg]`` and loads pinned weights; this stub
keeps the route contract testable without GPU in CI.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, List

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="TradeTalk TimesFM", version="0.1.0")

TOKEN = os.environ.get("TIMESFM_SERVICE_TOKEN", "").strip()
MODEL_LABEL = os.environ.get("TIMESFM_MODEL_LABEL", "timesfm-2.5-stub")


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> Dict[str, str]:
    return {"status": "ready", "model": MODEL_LABEL}


@app.get("/version")
def version() -> Dict[str, str]:
    return {
        "model": MODEL_LABEL,
        "weights_sha256": hashlib.sha256(MODEL_LABEL.encode()).hexdigest()[:16],
        "code_git_sha": os.environ.get("GIT_SHA", "unknown"),
    }


@app.post("/forecast")
def forecast(
    body: Dict[str, Any],
    authorization: str | None = Header(default=None),
) -> Dict[str, Any]:
    if TOKEN:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        got = authorization.split(" ", 1)[1].strip()
        if got != TOKEN:
            raise HTTPException(status_code=403, detail="invalid token")

    inputs: List[float] = body.get("inputs") or []
    horizon = int(body.get("horizon") or 20)
    if not inputs:
        raise HTTPException(status_code=400, detail="inputs required")

    last = float(inputs[-1])
    # Stub trajectory: slight upward drift with fake quantile channels (10).
    point = []
    quants = []
    for i in range(horizon):
        step = last * (1.0 + 0.001 * (i + 1))
        point.append(step)
        band = 0.02 * step
        row = [step, step - band, step - band * 0.6, step - band * 0.4, step - band * 0.2, step, step + band * 0.2, step + band * 0.4, step + band * 0.6, step + band]
        quants.append(row)

    return {
        "point": point,
        "quantiles": quants,
        "model_version": MODEL_LABEL,
        "served_at": time.time(),
    }
