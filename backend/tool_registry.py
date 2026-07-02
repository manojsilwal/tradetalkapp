"""
MCP-inspired internal tool registry: typed names, side-effect class, timeouts.

Connectors remain the source of truth; this layer adds contracts and safe invocation
for agent workflows without requiring separate MCP server processes.
"""
from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

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


class FetchOptionsFlowInput(BaseModel):
    ticker: str


class FetchFilingIntelligenceInput(BaseModel):
    ticker: str
    force_refresh: bool = False


def _build_registry() -> ToolRegistry:
    from .connectors.debate_data import fetch_debate_data
    from .connectors.macro import MacroHealthConnector
    from .connectors.options_flow import OptionsFlowConnector
    from .predictor.schemas import PredictorForecastToolInput

    reg = ToolRegistry()
    macro = MacroHealthConnector()
    options_flow = OptionsFlowConnector()

    async def _fetch_debate_data(ticker: str):
        return await fetch_debate_data(ticker)

    async def _macro_fetch():
        return await macro.fetch_data()

    async def _fetch_options_flow(ticker: str):
        if os.environ.get("OPTIONS_FLOW_ENABLE", "1").strip().lower() not in (
            "1", "true", "yes", "on",
        ):
            return {"available": False, "reason": "disabled", "ticker": ticker.upper()}
        return await options_flow.fetch_data(ticker=ticker)

    async def _fetch_filing_intelligence(ticker: str, force_refresh: bool = False):
        if os.environ.get("FILING_INTELLIGENCE_AGENT_TOOL", "0").strip().lower() not in (
            "1", "true", "yes", "on",
        ):
            return {"available": False, "reason": "disabled", "ticker": ticker.upper()}
        from .connectors.filing_intelligence import fetch_for_agent

        return await fetch_for_agent(ticker, force_refresh=force_refresh)

    async def _predictor_forecast_tool(
        ticker: str,
        horizons: Optional[List[str]] = None,
        as_of: Optional[str] = None,
    ):
        from . import deps
        from .predictor.agent import run_predictor_forecast

        _ = as_of  # reserved for point-in-time replay
        out = await run_predictor_forecast(
            ticker,
            horizons=horizons or ["1d", "5d", "21d", "63d"],
            tool_registry=deps.tool_registry,
            emit_ledger=True,
        )
        return out.model_dump(mode="json")

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
            default_timeout_s=90.0,
            description="Global macro snapshot (VIX, credit stress, etc.).",
        )
    )
    reg.register(
        ToolSpec(
            name="fetch_options_flow",
            handler=_fetch_options_flow,
            input_model=FetchOptionsFlowInput,
            side_effect=ToolSideEffect.READ,
            default_timeout_s=30.0,
            description="Free multi-provider options chain + EOD put/call & OI aggregates.",
        )
    )
    reg.register(
        ToolSpec(
            name="fetch_filing_intelligence",
            handler=_fetch_filing_intelligence,
            input_model=FetchFilingIntelligenceInput,
            side_effect=ToolSideEffect.READ,
            default_timeout_s=45.0,
            description="Structured filing-derived demand visibility, moat, and concentration.",
        )
    )
    reg.register(
        ToolSpec(
            name="predictor_forecast",
            handler=_predictor_forecast_tool,
            input_model=PredictorForecastToolInput,
            side_effect=ToolSideEffect.READ,
            default_timeout_s=90.0,
            description="Probabilistic price forecast (baselines + TimesFM path, evidence-gated).",
        )
    )
    return reg


registry = _build_registry()
