"""
Conformal band recalibration — the first closed self-learning loop (Phase 3).

Nightly, after the outcome grader writes ``forecast_band_hit`` rows, this
module measures the rolling empirical coverage of the predictor's q10–q90
band per horizon and computes a width *scale* that nudges coverage back
toward the 80 % target. The scale is persisted as a versioned TOOL resource
(``predictor_conformal``) in the RSPL registry, so:

* every change is semver-bumped with lineage (who/why/when),
* :func:`maybe_rollback` can restore the previous version if measured
  coverage regresses after a commit (mirrors ``SEPLKillSwitch``),
* decisions stamp the registry snapshot, tying each forecast to the exact
  calibration that shaped it.

Proportional controller, bounded:
    ``new_scale = clamp(old_scale * (1 + K * (target - coverage)), MIN, MAX)``

Kill switch: ``PREDICTOR_CONFORMAL_ENABLE=0`` → :func:`load_scales` returns
``{}`` and the predictor serves raw model bands.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

RESOURCE_NAME = "predictor_conformal"
TARGET_COVERAGE = 0.80
SCALE_MIN, SCALE_MAX = 0.5, 3.0
GAIN = 1.0  # proportional gain; 0.6 coverage @ 0.8 target → scale *= 1.2

_TRUTHY = ("1", "true", "yes", "on")


def conformal_enabled() -> bool:
    return (os.getenv("PREDICTOR_CONFORMAL_ENABLE", "1").strip().lower() or "1") in _TRUTHY


def _min_samples() -> int:
    try:
        return int(os.getenv("PREDICTOR_CONFORMAL_MIN_N", "20"))
    except ValueError:
        return 20


def _lookback_days() -> float:
    try:
        return float(os.getenv("PREDICTOR_CONFORMAL_LOOKBACK_DAYS", "90"))
    except ValueError:
        return 90.0


# ── Artifact load / apply ────────────────────────────────────────────────────


def _registry():
    from backend.resource_registry import get_resource_registry

    return get_resource_registry()


def load_artifact() -> Dict[str, Any]:
    """Active ``predictor_conformal`` body as a dict; ``{}`` when absent."""
    try:
        rec = _registry().get(RESOURCE_NAME)
        if rec is None:
            return {}
        body = json.loads(rec.body or "{}")
        return body if isinstance(body, dict) else {}
    except Exception as e:
        logger.debug("[Conformal] load_artifact failed: %s", e)
        return {}


def load_scales() -> Dict[str, float]:
    """``{horizon: scale}`` for band widening; empty when disabled/missing."""
    if not conformal_enabled():
        return {}
    body = load_artifact()
    horizons = body.get("horizons") or {}
    out: Dict[str, float] = {}
    for h, entry in horizons.items():
        try:
            s = float((entry or {}).get("scale"))
        except (TypeError, ValueError):
            continue
        if SCALE_MIN <= s <= SCALE_MAX:
            out[str(h)] = s
    return out


def apply_scale(
    q10: float, q50: float, q90: float, scale: float,
) -> tuple[float, float, float]:
    """Widen/narrow the band symmetrically around q50 by ``scale``."""
    lo = q50 - (q50 - q10) * scale
    hi = q50 + (q90 - q50) * scale
    return max(1e-8, lo), q50, max(hi, q50)


# ── Nightly update ───────────────────────────────────────────────────────────


def _coverage_by_horizon(ledger=None) -> Dict[str, Dict[str, float]]:
    """``{horizon: {coverage, n}}`` from recent ``forecast_band_hit`` rows."""
    from backend import decision_ledger as _dl

    try:
        ledger = ledger or _dl.get_ledger()
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception:
        return {}
    if conn is None:
        return {}
    cutoff = time.time() - _lookback_days() * 86400.0
    try:
        rows = conn.execute(
            """SELECT horizon, AVG(value) AS coverage, COUNT(*) AS n
               FROM outcome_observations
               WHERE metric = 'forecast_band_hit' AND as_of_ts >= ?
               GROUP BY horizon""",
            (cutoff,),
        ).fetchall()
    except Exception as e:
        logger.warning("[Conformal] coverage query failed: %s", e)
        return {}
    return {
        str(r["horizon"]): {"coverage": float(r["coverage"]), "n": int(r["n"])}
        for r in rows
        if r["coverage"] is not None
    }


def nightly_conformal_update(*, ledger=None, dry_run: bool = False) -> Dict[str, Any]:
    """Recompute per-horizon scales from graded coverage; commit to registry.

    Returns a summary dict (safe for scheduler logs). Never raises.
    """
    if not conformal_enabled():
        return {"enabled": False}
    try:
        stats = _coverage_by_horizon(ledger=ledger)
        if not stats:
            return {"enabled": True, "updated": False, "reason": "no graded forecast rows"}

        prev = load_artifact()
        prev_horizons = prev.get("horizons") or {}
        min_n = _min_samples()
        new_horizons: Dict[str, Any] = dict(prev_horizons)
        changed = False

        for h, s in stats.items():
            if s["n"] < min_n:
                continue
            old_scale = 1.0
            try:
                old_scale = float((prev_horizons.get(h) or {}).get("scale", 1.0))
            except (TypeError, ValueError):
                old_scale = 1.0
            new_scale = old_scale * (1.0 + GAIN * (TARGET_COVERAGE - s["coverage"]))
            new_scale = max(SCALE_MIN, min(SCALE_MAX, new_scale))
            entry = {
                "scale": round(new_scale, 4),
                "coverage_at_commit": round(s["coverage"], 4),
                "n": s["n"],
            }
            if entry != prev_horizons.get(h):
                changed = True
            new_horizons[h] = entry

        if not changed:
            return {"enabled": True, "updated": False, "reason": "no horizon met update criteria"}

        body = {
            "horizons": new_horizons,
            "target_coverage": TARGET_COVERAGE,
            "lookback_days": _lookback_days(),
            "updated_at": time.time(),
        }
        if dry_run:
            return {"enabled": True, "updated": False, "dry_run": True, "candidate_body": body}

        version = _commit_body(body, reason="nightly conformal recalibration")
        return {
            "enabled": True,
            "updated": True,
            "version": version,
            "horizons": {h: e.get("scale") for h, e in new_horizons.items()},
        }
    except Exception as e:
        logger.warning("[Conformal] nightly update failed: %s", e)
        return {"enabled": True, "error": str(e)[:300]}


def _commit_body(body: Dict[str, Any], *, reason: str) -> Optional[str]:
    from backend.resource_registry import ResourceKind, ResourceRecord

    reg = _registry()
    serialized = json.dumps(body, sort_keys=True)
    existing = reg.get(RESOURCE_NAME)
    if existing is None:
        rec = ResourceRecord(
            name=RESOURCE_NAME,
            kind=ResourceKind.TOOL,
            version="0.1.0",
            description="Conformal q10–q90 band scale per horizon, learned nightly from graded coverage.",
            learnable=True,
            body=serialized,
            metadata={"producer": "backend/predictor/conformal.py"},
        )
        reg.register(rec, actor="conformal_nightly", reason=reason)
        return rec.version
    updated = reg.update(
        RESOURCE_NAME,
        serialized,
        bump="patch",
        reason=reason,
        actor="conformal_nightly",
    )
    return updated.version


# ── Rollback guard (Phase 5) ─────────────────────────────────────────────────


def maybe_rollback(*, ledger=None, margin: float = 0.10) -> Dict[str, Any]:
    """Restore the previous artifact version if coverage regressed post-commit.

    Compares the coverage measured *now* against the coverage recorded at the
    last commit. If the distance from target worsened by more than ``margin``
    on any horizon with enough fresh samples, flip the active pointer back.
    """
    if not conformal_enabled():
        return {"enabled": False}
    try:
        reg = _registry()
        versions = reg.versions(RESOURCE_NAME)
        if len(versions) < 2:
            return {"enabled": True, "rolled_back": False, "reason": "no prior version"}
        body = load_artifact()
        prev_horizons = body.get("horizons") or {}
        current = _coverage_by_horizon(ledger=ledger)
        min_n = _min_samples()

        for h, entry in prev_horizons.items():
            cur = current.get(h)
            if not cur or cur["n"] < min_n:
                continue
            committed_cov = entry.get("coverage_at_commit")
            if committed_cov is None:
                continue
            dist_then = abs(TARGET_COVERAGE - float(committed_cov))
            dist_now = abs(TARGET_COVERAGE - cur["coverage"])
            if dist_now - dist_then > margin:
                prior = versions[1]  # versions sorted newest-first
                reg.restore(
                    RESOURCE_NAME,
                    prior,
                    reason=(
                        f"coverage regression on {h}: |target-cov| "
                        f"{dist_then:.3f} → {dist_now:.3f}"
                    ),
                    actor="conformal_killswitch",
                )
                logger.warning(
                    "[Conformal] rolled back to %s after regression on %s", prior, h,
                )
                return {
                    "enabled": True,
                    "rolled_back": True,
                    "restored_version": prior,
                    "horizon": h,
                }
        return {"enabled": True, "rolled_back": False}
    except Exception as e:
        logger.warning("[Conformal] maybe_rollback failed: %s", e)
        return {"enabled": True, "error": str(e)[:300]}
