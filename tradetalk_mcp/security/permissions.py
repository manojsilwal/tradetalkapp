"""Mode and action gating."""

from __future__ import annotations

from dataclasses import dataclass

from tradetalk_mcp.config import Settings


@dataclass(frozen=True)
class ActionGate:
    settings: Settings

    def service_tools_visible(self) -> bool:
        return self.settings.mode in ("actions", "full")

    def can_call_action(self, *, mutates: bool, requires_actions_enabled: bool) -> tuple[bool, str]:
        if not self.service_tools_visible():
            return False, "service tools disabled in TRADETALK_MCP_MODE=context (use actions or full)"
        if requires_actions_enabled and not self.settings.actions_enabled:
            return False, "set TRADETALK_MCP_ACTIONS_ENABLED=true for this action"
        if mutates and not self.settings.actions_enabled:
            return False, "mutating actions require TRADETALK_MCP_ACTIONS_ENABLED=true"
        return True, ""
