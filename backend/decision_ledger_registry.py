"""Registry attribution helpers for Decision-Outcome Ledger producers."""

from __future__ import annotations

import os
from typing import Dict, Iterable, Optional, Tuple


def registry_attribution(
    roles: Optional[Iterable[str]] = None,
) -> Tuple[Dict[str, str], str, str]:
    """
    Return ``(prompt_versions, registry_snapshot_id, model)`` for ledger emits.

    ``roles`` — when provided, only the prompt versions for the roles actually
    used by the decision are stamped (Phase F per-decision attribution), so
    SEPL kill-switch cohorts and model-swap replay segment precisely. Roles
    missing from the registry are stamped ``"unversioned"`` so the lineage
    still records *which* prompts ran. When ``roles`` is omitted the legacy
    behavior (stamp every active version) is preserved for older producers.

    Best-effort: empty dict / strings when the resource registry is disabled.
    """
    wanted = [r for r in (roles or []) if r]
    prompt_versions: Dict[str, str] = {}
    snap_id = ""
    try:
        from .resource_registry import get_resource_registry, registry_enabled

        if registry_enabled():
            reg = get_resource_registry()
            snap_id = reg.snapshot_id()
            all_versions = {r.name: r.version for r in reg.list()}
            if wanted:
                prompt_versions = {r: all_versions.get(r, "unversioned") for r in wanted}
            else:
                prompt_versions = all_versions
    except Exception:
        pass
    if wanted and not prompt_versions:
        # Registry disabled/unavailable — still record which roles produced
        # this decision so post-hoc analyses can cohort on prompt names.
        prompt_versions = {r: "unversioned" for r in wanted}
    # Resolve the model that would actually serve the call (provider cascade
    # aware) instead of blindly stamping OPENROUTER_MODEL — Phase 1 of the
    # model-agnostic harness needs trustworthy per-decision attribution.
    try:
        from .harness.backend_protocol import resolved_model_label

        model = resolved_model_label()
    except Exception:
        model = (os.getenv("OPENROUTER_MODEL") or os.getenv("GEMINI_MODEL") or "").strip()
    return prompt_versions, snap_id, model
