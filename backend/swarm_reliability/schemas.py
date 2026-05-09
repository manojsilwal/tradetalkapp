from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_json_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


class EvidenceArtifact(BaseModel):
    artifact_id: str
    source: str
    as_of: Optional[str] = None
    hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentExecutionRecord(BaseModel):
    schema_validated: bool = False
    tools_called: List[str] = Field(default_factory=list)
    output_hash: Optional[str] = None
    blocked_until_freshness_gate_passes: bool = False
    executed: bool = True


class EvidenceManifest(BaseModel):
    cycle_id: str
    generated_at: str = Field(default_factory=utc_now_iso)
    inputs: Dict[str, List[EvidenceArtifact]] = Field(default_factory=dict)
    agents: Dict[str, AgentExecutionRecord] = Field(default_factory=dict)

    def add_agent_output_hash(self, agent_name: str, output_payload: Any) -> None:
        rec = self.agents.get(agent_name) or AgentExecutionRecord()
        # Hashing is permitted only after schema validation to reduce noisy manifests.
        if not rec.schema_validated:
            return
        rec.output_hash = stable_json_hash(output_payload)
        self.agents[agent_name] = rec


class StaleSourceRecord(BaseModel):
    source: str
    as_of: Optional[str] = None
    threshold: str
    signal_type: str


class StaleDataReport(BaseModel):
    cycle_id: str
    status: str = "STALE_DATA"
    summoner_executed: bool = False
    affected_sources: List[StaleSourceRecord] = Field(default_factory=list)
    message: str = "Synthesis blocked because required evidence is stale."
    generated_at: str = Field(default_factory=utc_now_iso)


class StalenessThresholds(BaseModel):
    intraday_signal_max_age_minutes: Optional[int] = None
    daily_signal_max_age_hours: Optional[int] = None
    weekly_research_max_age_days: Optional[int] = None


class SourceStalenessPolicy(BaseModel):
    thresholds: Dict[str, StalenessThresholds] = Field(default_factory=dict)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def missing_evidence_refs(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    refs = payload.get("evidence_refs")
    if refs is None:
        return True
    return not isinstance(refs, list) or len(refs) == 0
