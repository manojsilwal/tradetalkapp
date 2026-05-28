"""Spawn and terminate harness sub-agents with A2A contract validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from ..config import HarnessConfig
from ..state import CRUDOperation, HarnessCRUDEdit, HarnessState, SubAgentRecord, new_id


class SubAgentManager:
    def __init__(self, config: HarnessConfig) -> None:
        self._config = config

    def _validate_a2a_contract(self, contract: Dict[str, Any]) -> bool:
        if not isinstance(contract, dict):
            return False
        required = {"source_agent", "target_agent"}
        return required.issubset(set(contract.keys()))

    def spawn(self, edit: HarnessCRUDEdit, state: HarnessState) -> HarnessState:
        active = [s for s in state.sub_agents if s.is_active]
        if len(active) >= self._config.max_concurrent_harness_agents:
            raise ValueError("max concurrent harness sub-agents reached")
        contract = dict(edit.payload.get("handoff_contract") or {})
        if not self._validate_a2a_contract(contract):
            raise ValueError("invalid A2A handoff contract")
        rec = SubAgentRecord(
            agent_id=edit.target_id or new_id(),
            name=str(edit.payload.get("name") or edit.target_id),
            role=str(edit.payload.get("role") or "specialist"),
            system_prompt=str(edit.payload.get("system_prompt") or ""),
            spawn_reason=edit.rationale,
            handoff_contract=contract,
        )
        return state.model_copy(update={"sub_agents": list(state.sub_agents) + [rec]})

    def terminate(self, edit: HarnessCRUDEdit, state: HarnessState) -> HarnessState:
        now = datetime.now(timezone.utc)
        updated: list[SubAgentRecord] = []
        for sa in state.sub_agents:
            if sa.agent_id == edit.target_id and sa.is_active:
                updated.append(
                    sa.model_copy(update={"is_active": False, "terminated_at": now})
                )
            else:
                updated.append(sa)
        return state.model_copy(update={"sub_agents": updated})

    def apply(self, edit: HarnessCRUDEdit, state: HarnessState) -> HarnessState:
        if edit.operation == CRUDOperation.DELETE:
            return self.terminate(edit, state)
        if edit.payload.get("terminate"):
            return self.terminate(edit, state)
        return self.spawn(edit, state)
