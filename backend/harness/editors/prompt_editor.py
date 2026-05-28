"""CRUD on harness system_prompts with price-guardrail validation."""

from __future__ import annotations

from ..state import CRUDOperation, HarnessCRUDEdit, HarnessState

REQUIRED_GUARDRAIL = (
    "You must NEVER state a price, percentage change, or numeric market value "
    "unless it is sourced directly from a tool call result in this conversation."
)

MARKET_AGENT_IDS = frozenset(
    {
        "gold_advisor",
        "data_ingest",
        "technical_analysis",
        "gold_analysis",
        "swarm_factor",
    }
)


class PromptEditor:
    def _validate_guardrails(self, new_prompt: str, agent_id: str) -> bool:
        if agent_id not in MARKET_AGENT_IDS:
            return True
        return REQUIRED_GUARDRAIL.lower() in (new_prompt or "").lower()

    def apply(self, edit: HarnessCRUDEdit, state: HarnessState) -> HarnessState:
        agent_id = edit.target_id
        prompts = dict(state.system_prompts)

        if edit.operation == CRUDOperation.DELETE:
            prompts.pop(agent_id, None)
        elif edit.operation == CRUDOperation.CREATE:
            body = str(edit.payload.get("system_prompt") or "")
            if not self._validate_guardrails(body, agent_id):
                raise ValueError("prompt missing required price guardrail")
            prompts[agent_id] = body
        elif edit.operation == CRUDOperation.UPDATE:
            body = str(edit.payload.get("system_prompt") or prompts.get(agent_id, ""))
            if not self._validate_guardrails(body, agent_id):
                raise ValueError("prompt missing required price guardrail")
            prompts[agent_id] = body

        return state.model_copy(update={"system_prompts": prompts})
