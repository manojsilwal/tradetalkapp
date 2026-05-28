"""Harness-level memory_overrides (does not delete base memory tiers)."""

from __future__ import annotations

from ..state import CRUDOperation, HarnessCRUDEdit, HarnessState


class MemoryEditor:
    def apply(self, edit: HarnessCRUDEdit, state: HarnessState) -> HarnessState:
        overrides = dict(state.memory_overrides)
        key = edit.target_id

        if edit.operation == CRUDOperation.DELETE:
            overrides.pop(key, None)
        else:
            overrides[key] = edit.payload.get("value", edit.payload)

        return state.model_copy(update={"memory_overrides": overrides})

    def as_retrieval_channel(self, state: HarnessState, query: str, top_k: int = 5) -> list[dict]:
        """Harness memory channel for RRF fusion (weight applied by caller)."""
        _ = query
        rows: list[dict] = []
        for key, value in (state.memory_overrides or {}).items():
            rows.append(
                {
                    "id": f"harness:{key}",
                    "document": str(value),
                    "metadata": {"channel": "harness", "key": key},
                    "collection": "harness_memory",
                }
            )
        return rows[:top_k]
