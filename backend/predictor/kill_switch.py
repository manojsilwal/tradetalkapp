"""
Operator-controlled feature flags for the predictor agent.

Pattern mirrors ``DECISION_LEDGER_ENABLE`` in
:mod:`backend.decision_ledger`: every flag check is a process-level env-var
read so flips in Render dashboard take effect at the next restart with no
code change.
"""
from __future__ import annotations

import os
from typing import Final

#: Full pipeline: features -> baselines -> TimesFM -> ensemble -> synth/review.
PREDICTOR_BACKEND_FULL: Final[str] = "full"

#: Skip TimesFM + LLM; serve the baseline ensemble with low confidence.
#: Useful when the microservice is degraded but we still want a forecast.
PREDICTOR_BACKEND_BASELINES_ONLY: Final[str] = "baselines_only"

#: Hard off — predictor returns the disabled payload, decision_terminal
#: falls back to the legacy heuristic. No model calls, no LLM calls.
PREDICTOR_BACKEND_NONE: Final[str] = "none"

_VALID_BACKENDS: Final[frozenset[str]] = frozenset({
    PREDICTOR_BACKEND_FULL,
    PREDICTOR_BACKEND_BASELINES_ONLY,
    PREDICTOR_BACKEND_NONE,
})


def predictor_enabled() -> bool:
    """Master switch — false -> :func:`predictor_backend` returns ``"none"``."""
    raw = (os.environ.get("PREDICTOR_ENABLE", "1").strip() or "1")
    return raw not in ("0", "false", "False", "no", "NO")


def predictor_backend() -> str:
    """
    Resolve the active backend. Honors:

    * ``PREDICTOR_ENABLE=0`` -> ``"none"``
    * ``PREDICTOR_BACKEND`` env var (one of ``full``, ``baselines_only``, ``none``)
    * default -> ``"full"``

    Unknown values fall back to ``"full"`` with a warning log; this keeps a
    typo from silently disabling the predictor.
    """
    if not predictor_enabled():
        return PREDICTOR_BACKEND_NONE
    raw = (os.environ.get("PREDICTOR_BACKEND", "") or "").strip().lower()
    if not raw:
        return PREDICTOR_BACKEND_FULL
    if raw not in _VALID_BACKENDS:
        # Defer logging until first access in the agent so importing this
        # module never has side effects (matters for the no-trade test).
        return PREDICTOR_BACKEND_FULL
    return raw


def cost_ceiling_usd() -> float:
    """Per-cycle cost ceiling. Default 0.05 USD."""
    raw = (os.environ.get("PREDICTOR_COST_CEILING_USD", "") or "").strip()
    try:
        v = float(raw) if raw else 0.05
    except ValueError:
        v = 0.05
    return max(0.0, v)
