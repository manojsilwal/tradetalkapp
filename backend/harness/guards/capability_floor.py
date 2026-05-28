"""Defer edits the configured model tier cannot reliably execute."""

from __future__ import annotations

from typing import Tuple

from ..state import CRUDOperation, HarnessCRUDEdit


class CapabilityFloorChecker:
    TIER_RANK = {"flash-lite": 0, "flash": 1, "pro": 2}

    def check(self, edit: HarnessCRUDEdit, model_tier: str) -> Tuple[bool, str]:
        tier = (model_tier or "pro").strip().lower()
        rank = self.TIER_RANK.get(tier, 1)
        op = edit.operation
        target = edit.target

        if target == "skill" and op in (CRUDOperation.CREATE, CRUDOperation.UPDATE):
            if edit.requires_capability == "code_generation_verified" or (
                edit.payload.get("skill_type") == "code"
            ):
                if rank < self.TIER_RANK["pro"]:
                    return False, "code skill edits require model_tier >= pro"
            if rank < self.TIER_RANK["flash"]:
                return False, "skill edits require model_tier >= flash"

        if target == "subagent" and op == CRUDOperation.CREATE:
            if rank < self.TIER_RANK["flash"]:
                return False, "subagent spawn requires model_tier >= flash"

        return True, "ok"
