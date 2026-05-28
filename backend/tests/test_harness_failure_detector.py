"""Failure signature detector tests."""

import os
import tempfile
import unittest

from backend.harness.config import HarnessConfig
from backend.harness.failure_detector import FailureSignatureDetector
from backend.harness.state import HarnessState, SubAgentRecord
from backend.harness.trajectory import TrajectoryBuffer, TrajectoryEvent, TrajectoryEventType


class TestFailureDetector(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = HarnessState(session_id="s")
        self.buf = TrajectoryBuffer(
            "s",
            db_path=os.path.join(self._tmp.name, "h.db"),
        )

    def _detector(self) -> FailureSignatureDetector:
        return FailureSignatureDetector(self.buf, self.state, HarnessConfig())

    def test_price_hallucination(self) -> None:
        self.buf.push(
            TrajectoryEvent(
                session_id="s",
                step=1,
                agent_id="gold_advisor",
                event_type=TrajectoryEventType.HALLUCINATION_FLAG,
                payload={},
            )
        )
        sigs = self._detector().run()
        ids = {s.signature_id for s in sigs}
        self.assertIn("PRICE_HALLUCINATION", ids)

    def test_routing_schema_mismatch(self) -> None:
        self.buf.push(
            TrajectoryEvent(
                session_id="s",
                step=1,
                agent_id="router",
                event_type=TrajectoryEventType.ROUTING_VIOLATION,
                payload={"reason": "schema"},
            )
        )
        sigs = self._detector().run()
        self.assertTrue(any(s.signature_id == "ROUTING_SCHEMA_MISMATCH" for s in sigs))

    def test_subagent_timeout(self) -> None:
        self.state = HarnessState(
            session_id="s",
            sub_agents=[
                SubAgentRecord(
                    agent_id="sub1",
                    name="sub",
                    role="r",
                    system_prompt="p",
                    is_active=True,
                )
            ],
        )
        sigs = FailureSignatureDetector(self.buf, self.state, HarnessConfig(subagent_timeout_seconds=0.0)).run()
        self.assertTrue(any(s.signature_id == "SUBAGENT_TIMEOUT" for s in sigs))
