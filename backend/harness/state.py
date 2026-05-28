"""Canonical harness state and refinement cycle models (Pydantic v2)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class SkillRecord(BaseModel):
    model_config = ConfigDict(strict=False)

    skill_id: str
    name: str
    version: int = 1
    source_code: str = ""
    skill_type: Literal["code", "heuristic"] = "heuristic"
    domain_tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    last_modified_at: datetime = Field(default_factory=_utc_now)
    usage_count: int = 0
    error_count: int = 0
    deprecated: bool = False


class SubAgentRecord(BaseModel):
    model_config = ConfigDict(strict=False)

    agent_id: str
    name: str
    role: str
    system_prompt: str
    spawned_at: datetime = Field(default_factory=_utc_now)
    terminated_at: Optional[datetime] = None
    spawn_reason: str = ""
    handoff_contract: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class HarnessState(BaseModel):
    model_config = ConfigDict(strict=False)

    session_id: str
    version: int = 0
    system_prompts: Dict[str, str] = Field(default_factory=dict)
    sub_agents: List[SubAgentRecord] = Field(default_factory=list)
    skills: List[SkillRecord] = Field(default_factory=list)
    memory_overrides: Dict[str, Any] = Field(default_factory=dict)
    last_refined_at: Optional[datetime] = None
    refinement_cycle_count: int = 0
    rollback_count: int = 0
    refinement_frozen: bool = False

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> "HarnessState":
        return cls.model_validate_json(raw)


class CRUDOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class HarnessCRUDEdit(BaseModel):
    model_config = ConfigDict(strict=False)

    edit_id: str = Field(default_factory=new_id)
    target: Literal["prompt", "skill", "memory", "subagent"]
    operation: CRUDOperation
    target_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    requires_capability: str = ""

    @field_validator("operation", mode="before")
    @classmethod
    def _coerce_operation(cls, v: Any) -> CRUDOperation:
        if isinstance(v, CRUDOperation):
            return v
        return CRUDOperation(str(v).lower())


class RefinementCycle(BaseModel):
    """Append-only after commit."""

    model_config = ConfigDict(strict=False, frozen=True)

    cycle_id: str = Field(default_factory=new_id)
    session_id: str
    triggered_at: datetime = Field(default_factory=_utc_now)
    failure_signatures: List[str] = Field(default_factory=list)
    proposed_edits: List[HarnessCRUDEdit] = Field(default_factory=list)
    applied_edits: List[HarnessCRUDEdit] = Field(default_factory=list)
    deferred_edits: List[HarnessCRUDEdit] = Field(default_factory=list)
    pre_cycle_eval_score: float = 0.5
    post_cycle_eval_score: Optional[float] = None
    rolled_back: bool = False
    observe_only: bool = True
    pre_cycle_version: int = 0

    def edits_json(self) -> str:
        return json.dumps(
            {
                "proposed": [e.model_dump(mode="json") for e in self.proposed_edits],
                "applied": [e.model_dump(mode="json") for e in self.applied_edits],
                "deferred": [e.model_dump(mode="json") for e in self.deferred_edits],
            },
            default=str,
        )
