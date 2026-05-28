"""Trajectory buffer tests."""

import os
import tempfile
import unittest

from backend.harness.trajectory import (
    TrajectoryBuffer,
    TrajectoryEvent,
    TrajectoryEventType,
    action_hash,
)


class TestTrajectoryBuffer(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.buf = TrajectoryBuffer(
            "sess-t",
            window_size=50,
            db_path=os.path.join(self._tmp.name, "h.db"),
        )

    def test_push_and_window(self) -> None:
        ev = TrajectoryEvent(
            session_id="sess-t",
            step=1,
            agent_id="a1",
            event_type=TrajectoryEventType.AGENT_ACTION,
            payload={"x": 1},
        )
        self.buf.push(ev)
        self.assertEqual(len(self.buf.get_window()), 1)

    def test_detect_loop(self) -> None:
        payload = {"act": "same"}
        h = action_hash("agent", "agent_action", payload)
        for i in range(4):
            self.buf.push(
                TrajectoryEvent(
                    session_id="sess-t",
                    step=i + 1,
                    agent_id="agent",
                    event_type=TrajectoryEventType.AGENT_ACTION,
                    payload=payload,
                )
            )
        self.assertTrue(self.buf.detect_loop("agent", lookback=20, threshold=3))

    def test_price_output_requires_tool_ref(self) -> None:
        with self.assertRaises(ValueError):
            TrajectoryEvent(
                session_id="sess-t",
                step=1,
                agent_id="g",
                event_type=TrajectoryEventType.PRICE_OUTPUT,
                payload={},
            )
