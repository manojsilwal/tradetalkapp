"""Registry attribution helpers for Decision-Outcome Ledger producers."""

from __future__ import annotations

import os
from typing import Dict, Tuple


def registry_attribution() -> Tuple[Dict[str, str], str, str]:
    """
    Return ``(prompt_versions, registry_snapshot_id, model)`` for ledger emits.

    Best-effort: empty dict / strings when the resource registry is disabled.
    """
    prompt_versions: Dict[str, str] = {}
    snap_id = ""
    try:
        from .resource_registry import get_resource_registry, registry_enabled

        if registry_enabled():
            reg = get_resource_registry()
            snap_id = reg.snapshot_id()
            prompt_versions = {r.name: r.version for r in reg.list()}
    except Exception:
        pass
    model = (os.getenv("OPENROUTER_MODEL") or os.getenv("GEMINI_MODEL") or "").strip()
    return prompt_versions, snap_id, model
