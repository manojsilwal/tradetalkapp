"""Production-first harness configuration with minimal knobs."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel


class HarnessConfig(BaseModel):
    # Single optimal production route defaults.
    refinement_frequency_steps: int = 50
    trajectory_window_size: int = 500
    rollback_degradation_threshold: float = 0.1
    rollback_eval_window_steps: int = 20
    max_rollbacks_per_session: int = 3
    max_concurrent_harness_agents: int = 2
    skill_sandbox_timeout_seconds: int = 5
    subagent_drain_grace_period_seconds: int = 10
    model_tier: Literal["pro", "flash", "flash-lite"] = "flash-lite"
    db_path: str = "harness.db"
    enable_emergency_refinement: bool = True
    mutation_enable: bool = True
    observe_only: bool = False
    loop_detect_threshold: int = 3
    loop_detect_lookback: int = 20
    stale_data_max_age_seconds: float = 3600.0
    low_confidence_threshold: float = 0.35
    low_confidence_stall_steps: int = 5
    memory_rrf_floor: float = 0.01
    subagent_timeout_seconds: float = 120.0
    mutation_engine_export_path: str = "mutation_engine/harness_input.json"


def harness_config_from_env() -> HarnessConfig:
    """
    Production-only profile with minimal overrides.
    We intentionally avoid staging/profile branching.
    """
    db_path = os.environ.get("HARNESS_DB_PATH", "harness.db").strip() or "harness.db"
    return HarnessConfig(db_path=db_path)
