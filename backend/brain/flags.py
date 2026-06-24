"""Per-surface cutover flags for routing user-facing surfaces to the brain.

All default OFF so production behavior is unchanged until a flag is flipped.
``BRAIN_CUTOVER_ALL=1`` turns every surface on at once; per-surface env vars
(e.g. ``BRAIN_CUTOVER_SCORECARD=1``) override individually.

Surfaces: decision_terminal, scorecard, actionable, daily_brief, swarm,
debate, predictor.
"""
from __future__ import annotations

import os

_SURFACES = {
    "decision_terminal", "scorecard", "actionable", "daily_brief",
    "swarm", "debate", "predictor",
}


def brain_surface_enabled(surface: str) -> bool:
    """True if the brain should serve ``surface`` (requires serving enabled)."""
    if os.environ.get("BRAIN_SERVE_ENABLE", "0") != "1":
        return False
    if os.environ.get("BRAIN_CUTOVER_ALL", "0") == "1":
        return True
    key = f"BRAIN_CUTOVER_{surface.upper()}"
    return os.environ.get(key, "0") == "1"
