"""
Learned ensemble weights — second self-learning loop (Phase 3).

``weighted_inverse_mase`` computes per-request weights from a single MASE
snapshot, which is noisy. This module learns *persistent* per-horizon member
weights from walk-forward replay over data-lake history: at each anchor date
every baseline member predicts the close ``H`` trading days ahead using only
prior data, errors accumulate, and weights = normalized inverse mean
absolute percentage error.

The artifact is a versioned TOOL resource (``predictor_ensemble_weights``)
in the RSPL registry — same lineage/rollback semantics as the conformal
artifact. At request time :func:`blend_weights` averages the learned prior
with the request-local inverse-MASE weights, so a regime break still moves
weights immediately while the prior suppresses single-window noise.

Kill switch: ``PREDICTOR_LEARNED_WEIGHTS_ENABLE=0`` → :func:`load_weights`
returns ``{}`` and request-local weights are used unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RESOURCE_NAME = "predictor_ensemble_weights"
HORIZON_TD = {"1d": 1, "5d": 5, "21d": 21, "63d": 63}

_TRUTHY = ("1", "true", "yes", "on")


def learned_weights_enabled() -> bool:
    return (
        os.getenv("PREDICTOR_LEARNED_WEIGHTS_ENABLE", "1").strip().lower() or "1"
    ) in _TRUTHY


def _registry():
    from backend.resource_registry import get_resource_registry

    return get_resource_registry()


# ── Load / blend ─────────────────────────────────────────────────────────────


def load_weights() -> Dict[str, Dict[str, float]]:
    """``{horizon: {member: weight}}``; empty when disabled or absent."""
    if not learned_weights_enabled():
        return {}
    try:
        rec = _registry().get(RESOURCE_NAME)
        if rec is None:
            return {}
        body = json.loads(rec.body or "{}")
        horizons = body.get("horizons") or {}
        out: Dict[str, Dict[str, float]] = {}
        for h, members in horizons.items():
            if not isinstance(members, dict):
                continue
            clean = {}
            for m, w in members.items():
                try:
                    clean[str(m)] = float(w)
                except (TypeError, ValueError):
                    continue
            if clean:
                out[str(h)] = clean
        return out
    except Exception as e:
        logger.debug("[LearnedWeights] load failed: %s", e)
        return {}


def blend_weights(
    request_weights: Dict[str, float],
    learned: Dict[str, float],
    *,
    prior_strength: float = 0.5,
) -> Dict[str, float]:
    """Average learned prior with request-local weights over shared members.

    Members absent from the learned prior (e.g. ``timesfm_mean``, which can't
    be replayed historically without the service) keep their request weight.
    The result is renormalized to sum 1.
    """
    if not learned:
        return dict(request_weights)
    merged: Dict[str, float] = {}
    for member, req_w in request_weights.items():
        lw = learned.get(member)
        if lw is None:
            merged[member] = req_w
        else:
            merged[member] = (1.0 - prior_strength) * req_w + prior_strength * lw
    s = sum(merged.values()) or 1.0
    return {k: v / s for k, v in merged.items()}


# ── Nightly walk-forward update ──────────────────────────────────────────────


def _available_lake_tickers(limit: int) -> List[str]:
    try:
        from backend.data_lake.config import PRICES_DIR

        if not os.path.isdir(PRICES_DIR):
            return []
        names = sorted(
            f[:-8] for f in os.listdir(PRICES_DIR) if f.endswith(".parquet")
        )
        return names[: max(1, limit)]
    except Exception:
        return []


def _load_series(ticker: str) -> Optional["Any"]:
    try:
        import pandas as pd

        from backend.data_lake.config import PRICES_DIR

        path = os.path.join(PRICES_DIR, f"{ticker.upper()}.parquet")
        if not os.path.isfile(path):
            return None
        df = pd.read_parquet(path, columns=["Close"])
        if df.empty or len(df) < 128:
            return None
        return df["Close"].astype(float).values
    except Exception:
        return None


def nightly_weights_update(
    *,
    tickers: Optional[List[str]] = None,
    lookback_anchors: int = 8,
    anchor_step_td: int = 21,
    max_tickers: int = 25,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Walk-forward error accumulation → inverse-error weights → registry.

    Pure data-lake math (no network, no LLM); a sample of tickers keeps the
    nightly cost bounded on small Cloud Run instances.
    """
    if not learned_weights_enabled():
        return {"enabled": False}
    try:
        import numpy as np

        from .baselines import (
            drift_forecast, ewma_forecast, naive_forecast, seasonal_naive_forecast,
        )

        members = {
            "naive": naive_forecast,
            "seasonal_naive": seasonal_naive_forecast,
            "ewma": ewma_forecast,
            "drift": drift_forecast,
        }

        sample = tickers or _available_lake_tickers(max_tickers)
        if not sample:
            return {"enabled": True, "updated": False, "reason": "no data-lake tickers"}

        # errors[horizon][member] = list of abs pct errors
        errors: Dict[str, Dict[str, List[float]]] = {
            h: {m: [] for m in members} for h in HORIZON_TD
        }
        n_samples = 0

        for t in sample:
            series = _load_series(t)
            if series is None:
                continue
            n = len(series)
            max_td = max(HORIZON_TD.values())
            for k in range(1, lookback_anchors + 1):
                anchor = n - max_td - k * anchor_step_td
                if anchor < 64:
                    break
                history = np.asarray(series[:anchor], dtype=np.float64)
                for h_label, td in HORIZON_TD.items():
                    realized_idx = anchor + td - 1
                    if realized_idx >= n:
                        continue
                    realized = float(series[realized_idx])
                    if realized <= 0:
                        continue
                    for m_name, fn in members.items():
                        try:
                            pred = float(fn(history, td))
                        except Exception:
                            continue
                        errors[h_label][m_name].append(abs(pred - realized) / realized)
                        n_samples += 1

        horizons_out: Dict[str, Dict[str, float]] = {}
        for h_label, per_member in errors.items():
            inv: Dict[str, float] = {}
            for m_name, errs in per_member.items():
                if len(errs) < 5:
                    continue
                mean_err = sum(errs) / len(errs)
                inv[m_name] = 1.0 / (mean_err + 1e-6)
            s = sum(inv.values())
            if not inv or s <= 0:
                continue
            horizons_out[h_label] = {m: round(w / s, 6) for m, w in inv.items()}

        if not horizons_out:
            return {"enabled": True, "updated": False, "reason": "insufficient samples"}

        body = {
            "horizons": horizons_out,
            "n_samples": n_samples,
            "n_tickers": len(sample),
            "updated_at": time.time(),
        }
        if dry_run:
            return {"enabled": True, "updated": False, "dry_run": True, "candidate_body": body}

        version = _commit_body(body)
        return {
            "enabled": True,
            "updated": True,
            "version": version,
            "n_samples": n_samples,
            "horizons": list(horizons_out.keys()),
        }
    except Exception as e:
        logger.warning("[LearnedWeights] nightly update failed: %s", e)
        return {"enabled": True, "error": str(e)[:300]}


def _commit_body(body: Dict[str, Any]) -> Optional[str]:
    from backend.resource_registry import ResourceKind, ResourceRecord

    reg = _registry()
    serialized = json.dumps(body, sort_keys=True)
    existing = reg.get(RESOURCE_NAME)
    if existing is None:
        rec = ResourceRecord(
            name=RESOURCE_NAME,
            kind=ResourceKind.TOOL,
            version="0.1.0",
            description="Per-horizon ensemble member weights learned from walk-forward data-lake replay.",
            learnable=True,
            body=serialized,
            metadata={"producer": "backend/predictor/learned_weights.py"},
        )
        reg.register(rec, actor="learned_weights_nightly", reason="initial learned weights")
        return rec.version
    updated = reg.update(
        RESOURCE_NAME,
        serialized,
        bump="patch",
        reason="nightly walk-forward weight refresh",
        actor="learned_weights_nightly",
    )
    return updated.version
