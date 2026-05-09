"""Load and validate .tradetalk/mcp-actions.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ActionRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ActionDef:
    name: str
    method: str
    path: str
    enabled: bool
    mutates: bool
    requires_actions_enabled: bool


@dataclass
class ActionRegistry:
    version: int
    actions: list[ActionDef]

    def by_name(self) -> dict[str, ActionDef]:
        return {a.name: a for a in self.actions if a.enabled}


def load_action_registry(repo_root: str) -> ActionRegistry:
    path = Path(repo_root) / ".tradetalk" / "mcp-actions.json"
    if not path.is_file():
        return ActionRegistry(version=1, actions=[])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ActionRegistryError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ActionRegistryError("registry root must be an object")
    version = int(data.get("version", 1))
    raw_actions = data.get("actions", [])
    if not isinstance(raw_actions, list):
        raise ActionRegistryError("'actions' must be a list")
    actions: list[ActionDef] = []
    for i, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            raise ActionRegistryError(f"actions[{i}] must be an object")
        name = str(item.get("name", "")).strip()
        method = str(item.get("method", "GET")).strip().upper()
        rpath = str(item.get("path", "")).strip()
        if not name or not rpath:
            raise ActionRegistryError(f"actions[{i}] needs name and path")
        if not rpath.startswith("/"):
            raise ActionRegistryError(f"actions[{i}] path must start with /")
        actions.append(
            ActionDef(
                name=name,
                method=method,
                path=rpath,
                enabled=bool(item.get("enabled", True)),
                mutates=bool(item.get("mutates", False)),
                requires_actions_enabled=bool(item.get("requires_actions_enabled", True)),
            )
        )
    return ActionRegistry(version=version, actions=actions)


def validate_registry_schema(repo_root: str) -> list[str]:
    """Return list of error strings; empty if OK."""
    try:
        load_action_registry(repo_root)
    except ActionRegistryError as e:
        return [str(e)]
    return []
