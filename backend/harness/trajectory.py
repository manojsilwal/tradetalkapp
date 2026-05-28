"""Trajectory capture for the continual harness layer."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class TrajectoryEventType(str, Enum):
    AGENT_ACTION = "agent_action"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ROUTING_DECISION = "routing_decision"
    ROUTING_VIOLATION = "routing_violation"
    AGENT_ERROR = "agent_error"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    SKILL_INVOKED = "skill_invoked"
    SKILL_ERROR = "skill_error"
    PRICE_OUTPUT = "price_output"
    HALLUCINATION_FLAG = "hallucination_flag"
    REFINER_PROPOSAL = "refiner_proposal"
    HARNESS_EDIT_APPLIED = "harness_edit_applied"
    ROLLBACK_TRIGGERED = "rollback_triggered"


class TrajectoryEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    step: int
    agent_id: str
    event_type: TrajectoryEventType
    payload: Dict[str, Any] = Field(default_factory=dict)
    tool_call_ref: Optional[str] = None
    trace_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    eval_score: Optional[float] = None

    @model_validator(mode="after")
    def _price_output_requires_tool_ref(self) -> "TrajectoryEvent":
        if self.event_type == TrajectoryEventType.PRICE_OUTPUT and not self.tool_call_ref:
            raise ValueError("PRICE_OUTPUT requires tool_call_ref")
        return self


def action_hash(agent_id: str, event_type: str, payload: Dict[str, Any]) -> str:
    blob = json.dumps(
        {"a": agent_id, "t": event_type, "p": payload},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class TrajectoryBuffer:
    """Ring buffer with optional SQLite flush for events beyond window."""

    def __init__(
        self,
        session_id: str,
        *,
        window_size: int = 500,
        db_path: str = "harness.db",
    ) -> None:
        self.session_id = session_id
        self.window_size = max(10, int(window_size))
        self.db_path = db_path
        self._lock = threading.RLock()
        self._events: Deque[TrajectoryEvent] = deque(maxlen=self.window_size)
        self._step = 0
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS harness_trajectory_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    agent_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT,
                    tool_call_ref TEXT,
                    trace_id TEXT,
                    timestamp TEXT,
                    eval_score REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hte_session_step "
                "ON harness_trajectory_events(session_id, step)"
            )
            conn.commit()

    def next_step(self) -> int:
        with self._lock:
            self._step += 1
            return self._step

    def push(self, event: TrajectoryEvent) -> None:
        with self._lock:
            if event.step <= 0:
                event = event.model_copy(update={"step": self._step or self.next_step()})
            self._events.append(event)
            try:
                with self._conn() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO harness_trajectory_events
                        (event_id, session_id, step, agent_id, event_type, payload_json,
                         tool_call_ref, trace_id, timestamp, eval_score)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event.session_id,
                            event.step,
                            event.agent_id,
                            event.event_type.value,
                            json.dumps(event.payload, default=str),
                            event.tool_call_ref,
                            event.trace_id,
                            event.timestamp.isoformat(),
                            event.eval_score,
                        ),
                    )
                    conn.commit()
            except Exception as e:
                logger.debug("[Harness] trajectory flush failed: %s", e)

    def get_window(
        self,
        *,
        last_n: Optional[int] = None,
        since_step: Optional[int] = None,
    ) -> List[TrajectoryEvent]:
        with self._lock:
            rows = list(self._events)
        if since_step is not None:
            rows = [e for e in rows if e.step >= since_step]
        if last_n is not None:
            rows = rows[-last_n:]
        return rows

    def get_failure_window(self) -> List[TrajectoryEvent]:
        failure_types = {
            TrajectoryEventType.AGENT_ERROR,
            TrajectoryEventType.ROUTING_VIOLATION,
            TrajectoryEventType.HALLUCINATION_FLAG,
            TrajectoryEventType.SKILL_ERROR,
            TrajectoryEventType.ROLLBACK_TRIGGERED,
        }
        return [
            e
            for e in self.get_window()
            if e.event_type in failure_types
            or (e.payload or {}).get("is_failure")
        ]

    def detect_loop(
        self,
        agent_id: str,
        *,
        lookback: int = 20,
        threshold: int = 3,
    ) -> bool:
        window = self.get_window(last_n=lookback)
        counts: Dict[str, int] = {}
        for ev in window:
            if ev.agent_id != agent_id:
                continue
            h = action_hash(ev.agent_id, ev.event_type.value, ev.payload)
            counts[h] = counts.get(h, 0) + 1
            if counts[h] > threshold:
                return True
        return False

    def recent_tool_call_refs(self, *, last_n: int = 50) -> set[str]:
        refs: set[str] = set()
        for ev in self.get_window(last_n=last_n):
            if ev.tool_call_ref:
                refs.add(ev.tool_call_ref)
            if ev.event_type == TrajectoryEventType.TOOL_CALL:
                tid = str((ev.payload or {}).get("tool_call_id") or "")
                if tid:
                    refs.add(tid)
        return refs
