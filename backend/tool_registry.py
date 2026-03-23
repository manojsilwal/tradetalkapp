"""
MCP-inspired internal tool registry: typed names, side-effect class, timeouts.

Connectors remain the source of truth; this layer adds contracts and safe invocation
for agent workflows without requiring separate MCP server processes.
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, Type

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class ToolSideEffect(str, Enum):
    """Maps to PDF-style tool classes (read vs write vs sensitive)."""

    READ = "read"
    IDEMPOTENT_WRITE = "idempotent_write"
    SENSITIVE = "sensitive"


class ToolSpec:
    def __init__(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        input_model: Type[BaseModel],
        side_effect: ToolSideEffect,
        default_timeout_s: float = 60.0,
        description: str = "",
    ):
        self.name = name
        self.handler = handler
        self.input_model = input_model
        self.side_effect = side_effect
        self.default_timeout_s = default_timeout_s
        self.description = description


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    async def invoke(
        self,
        name: str,
        payload: dict,
        *,
        timeout_s: Optional[float] = None,
    ) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"Unknown tool: {name}")
        validated = spec.input_model.model_validate(payload)
        to = timeout_s if timeout_s is not None else spec.default_timeout_s
        coro = spec.handler(**validated.model_dump())
        return await asyncio.wait_for(coro, timeout=to)


# ── Input models (minimal, explicit) ───────────────────────────────────────────


class FetchDebateDataInput(BaseModel):
    ticker: str


class MacroFetchInput(BaseModel):
    """No fields — macro connector is global snapshot."""

    model_config = ConfigDict(extra="forbid")


def _build_registry() -> ToolRegistry:
    from .connectors.debate_data import fetch_debate_data
    from .connectors.macro import MacroHealthConnector

    reg = ToolRegistry()
    macro = MacroHealthConnector()

    async def _fetch_debate_data(ticker: str):
        return await fetch_debate_data(ticker)

    async def _macro_fetch():
        return await macro.fetch_data()

    reg.register(
        ToolSpec(
            name="fetch_debate_data",
            handler=_fetch_debate_data,
            input_model=FetchDebateDataInput,
            side_effect=ToolSideEffect.READ,
            default_timeout_s=90.0,
            description="Live debate inputs (price, fundamentals proxy) for a ticker.",
        )
    )
    reg.register(
        ToolSpec(
            name="macro_fetch",
            handler=_macro_fetch,
            input_model=MacroFetchInput,
            side_effect=ToolSideEffect.READ,
            default_timeout_s=45.0,
            description="Global macro snapshot (VIX, credit stress, etc.).",
        )
    )
    return reg


registry = _build_registry()
