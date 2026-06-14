"""Pydantic contracts for the stock predictor."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


HorizonLabel = Literal["1d", "5d", "21d", "63d"]


class HorizonBandUsd(BaseModel):
    """Single horizon fan-chart band (80 % interval q10–q90)."""

    model_config = ConfigDict(extra="forbid")

    horizon: str
    q10_usd: Optional[float] = None
    q50_usd: Optional[float] = None
    q90_usd: Optional[float] = None
    point_usd: Optional[float] = None


class PredictorForecastResponse(BaseModel):
    """Public API / decision-terminal payload fragment."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "disabled", "degraded", "stale_data", "insufficient_data"] = "ok"
    ticker: str
    cycle_id: str
    disclaimer: str
    model_version: str = ""
    model_confidence: Literal["low", "medium", "high"] = "medium"
    directional_bias: Literal["up", "down", "flat", "mixed"] = "flat"
    horizon_bands_usd: List[HorizonBandUsd] = Field(default_factory=list)
    bull_price_usd_3y_scenario: Optional[float] = None
    base_price_usd_3y_scenario: Optional[float] = None
    bear_price_usd_3y_scenario: Optional[float] = None
    assumptions: List[str] = Field(default_factory=list)
    synthesis_summary: str = ""
    reviewer_summary: str = ""
    executed: bool = True
    blocked_until_freshness_gate_passes: bool = False
    ensemble_weights: Dict[str, float] = Field(default_factory=dict)
    input_hash: str = ""
    config_hash: str = ""
    shadow_diff_logged: bool = False
    meta: Dict[str, Any] = Field(default_factory=dict)
    data_freshness: Optional[Dict[str, Any]] = None


class PredictorForecastToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    horizons: List[str] = Field(default_factory=lambda: ["1d", "5d", "21d", "63d"])
    as_of: Optional[str] = Field(
        default=None,
        description="ISO date optional as-of for replay",
    )


class TimesFMForecastInput(BaseModel):
    """Microservice request shape."""

    model_config = ConfigDict(extra="forbid")

    inputs: List[float]
    horizon: int
    config_hash: str = ""
    model_version: str = ""
