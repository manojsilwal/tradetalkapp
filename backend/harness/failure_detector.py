"""Deterministic failure signature detection over trajectory windows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .config import HarnessConfig
from .state import HarnessState
from .trajectory import TrajectoryBuffer, TrajectoryEventType, action_hash


class FailureSignature(BaseModel):
    signature_id: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: Literal["low", "medium", "high", "critical"]
    affected_agent_ids: List[str] = Field(default_factory=list)
    raw_evidence: List[str] = Field(default_factory=list)
    recommended_crud_target: Literal["prompt", "skill", "memory", "subagent"] = "prompt"
    description: str = ""


class FailureSignatureDetector:
    def __init__(
        self,
        buffer: TrajectoryBuffer,
        harness_state: HarnessState,
        config: Optional[HarnessConfig] = None,
    ) -> None:
        self._buffer = buffer
        self._state = harness_state
        self._config = config or HarnessConfig()

    def run(self) -> List[FailureSignature]:
        detectors = [
            self._detect_price_hallucination,
            self._detect_agent_loop,
            self._detect_routing_schema_mismatch,
            self._detect_skill_error_repeated,
            self._detect_low_confidence_stall,
            self._detect_stale_data,
            self._detect_memory_retrieval_miss,
            self._detect_subagent_timeout,
        ]
        out: List[FailureSignature] = []
        for fn in detectors:
            sig = fn()
            if sig is not None:
                out.append(sig)
        return out

    def _detect_price_hallucination(self) -> Optional[FailureSignature]:
        flags = [
            e
            for e in self._buffer.get_window()
            if e.event_type == TrajectoryEventType.HALLUCINATION_FLAG
        ]
        if not flags:
            return None
        return FailureSignature(
            signature_id="PRICE_HALLUCINATION",
            severity="critical",
            affected_agent_ids=sorted({e.agent_id for e in flags}),
            raw_evidence=[e.event_id for e in flags],
            recommended_crud_target="prompt",
            description="Numeric market output without verified tool_call_ref.",
        )

    def _detect_agent_loop(self) -> Optional[FailureSignature]:
        agents = sorted({e.agent_id for e in self._buffer.get_window() if e.agent_id})
        for aid in agents:
            if self._buffer.detect_loop(
                aid,
                lookback=self._config.loop_detect_lookback,
                threshold=self._config.loop_detect_threshold,
            ):
                evs = self._buffer.get_window(last_n=self._config.loop_detect_lookback)
                evs = [e for e in evs if e.agent_id == aid]
                return FailureSignature(
                    signature_id="AGENT_LOOP",
                    severity="high",
                    affected_agent_ids=[aid],
                    raw_evidence=[e.event_id for e in evs[-5:]],
                    recommended_crud_target="prompt",
                    description=f"Agent {aid} repeated the same action hash > threshold.",
                )
        return None

    def _detect_routing_schema_mismatch(self) -> Optional[FailureSignature]:
        violations = [
            e
            for e in self._buffer.get_window()
            if e.event_type == TrajectoryEventType.ROUTING_VIOLATION
            and (e.payload or {}).get("reason") == "schema"
        ]
        if not violations:
            return None
        return FailureSignature(
            signature_id="ROUTING_SCHEMA_MISMATCH",
            severity="critical",
            affected_agent_ids=sorted({e.agent_id for e in violations}),
            raw_evidence=[e.event_id for e in violations],
            recommended_crud_target="prompt",
            description="Routing gate rejected handoff due to schema mismatch.",
        )

    def _detect_skill_error_repeated(self) -> Optional[FailureSignature]:
        counts: dict[str, int] = {}
        evidence: dict[str, list[str]] = {}
        for e in self._buffer.get_window():
            if e.event_type != TrajectoryEventType.SKILL_ERROR:
                continue
            sid = str((e.payload or {}).get("skill_id") or "unknown")
            counts[sid] = counts.get(sid, 0) + 1
            evidence.setdefault(sid, []).append(e.event_id)
        for sid, n in counts.items():
            if n > 2:
                return FailureSignature(
                    signature_id="SKILL_ERROR_REPEATED",
                    severity="medium",
                    affected_agent_ids=[],
                    raw_evidence=evidence.get(sid, []),
                    recommended_crud_target="skill",
                    description=f"Skill {sid} failed {n} times in window.",
                )
        return None

    def _detect_low_confidence_stall(self) -> Optional[FailureSignature]:
        streak = 0
        agents: list[str] = []
        evidence: list[str] = []
        for e in sorted(self._buffer.get_window(), key=lambda x: x.step):
            conf = e.eval_score
            if conf is None:
                conf = (e.payload or {}).get("confidence")
            try:
                c = float(conf) if conf is not None else 1.0
            except (TypeError, ValueError):
                c = 1.0
            if c < self._config.low_confidence_threshold:
                streak += 1
                agents.append(e.agent_id)
                evidence.append(e.event_id)
            else:
                streak = 0
                agents = []
                evidence = []
            if streak >= self._config.low_confidence_stall_steps:
                return FailureSignature(
                    signature_id="LOW_CONFIDENCE_STALL",
                    severity="medium",
                    affected_agent_ids=sorted(set(agents)),
                    raw_evidence=evidence,
                    recommended_crud_target="prompt",
                    description="Confidence below threshold for consecutive steps.",
                )
        return None

    def _detect_stale_data(self) -> Optional[FailureSignature]:
        now = datetime.now(timezone.utc).timestamp()
        stale: list = []
        for e in self._buffer.get_window():
            if e.event_type != TrajectoryEventType.TOOL_RESULT:
                continue
            ts = (e.payload or {}).get("data_timestamp")
            if ts is None:
                continue
            try:
                age = now - float(ts)
            except (TypeError, ValueError):
                continue
            if age > self._config.stale_data_max_age_seconds:
                stale.append(e)
        if not stale:
            return None
        return FailureSignature(
            signature_id="STALE_DATA_DEPENDENCY",
            severity="high",
            affected_agent_ids=sorted({e.agent_id for e in stale}),
            raw_evidence=[e.event_id for e in stale],
            recommended_crud_target="memory",
            description="Tool result timestamps exceed staleness threshold.",
        )

    def _detect_memory_retrieval_miss(self) -> Optional[FailureSignature]:
        misses = [
            e
            for e in self._buffer.get_window()
            if e.event_type == TrajectoryEventType.MEMORY_READ
            and float((e.payload or {}).get("rrf_score", 1.0)) < self._config.memory_rrf_floor
        ]
        if not misses:
            return None
        return FailureSignature(
            signature_id="MEMORY_RETRIEVAL_MISS",
            severity="low",
            affected_agent_ids=sorted({e.agent_id for e in misses}),
            raw_evidence=[e.event_id for e in misses],
            recommended_crud_target="memory",
            description="RRF retrieval score below floor across memory read.",
        )

    def _detect_subagent_timeout(self) -> Optional[FailureSignature]:
        now = datetime.now(timezone.utc).timestamp()
        active = [sa for sa in self._state.sub_agents if sa.is_active]
        if not active:
            return None
        last_by_agent: dict[str, float] = {}
        for e in self._buffer.get_window():
            last_by_agent[e.agent_id] = e.timestamp.timestamp()
        timed_out: list[str] = []
        for sa in active:
            last = last_by_agent.get(sa.agent_id)
            if last is None or (now - last) > self._config.subagent_timeout_seconds:
                timed_out.append(sa.agent_id)
        if not timed_out:
            return None
        return FailureSignature(
            signature_id="SUBAGENT_TIMEOUT",
            severity="high",
            affected_agent_ids=timed_out,
            raw_evidence=timed_out,
            recommended_crud_target="subagent",
            description="Harness sub-agent active but no recent trajectory events.",
        )
