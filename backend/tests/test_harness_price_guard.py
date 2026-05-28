"""Price output guard tests."""

import os
import tempfile
import unittest

from backend.harness.guards.price_output_guard import PriceOutputGuard
from backend.harness.trajectory import TrajectoryBuffer, TrajectoryEventType


class TestPriceOutputGuard(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.buf = TrajectoryBuffer(
            "s",
            db_path=os.path.join(self._tmp.name, "h.db"),
        )
        self.guard = PriceOutputGuard(self.buf, agent_id="gold_advisor", session_id="s")

    def test_blocks_fabricated_price(self) -> None:
        text = "Gold is trading at $2,400 today."
        out = self.guard.sanitize_text(text)
        self.assertIn("[PRICE_REDACTED]", out)
        flags = [
            e
            for e in self.buf.get_window()
            if e.event_type == TrajectoryEventType.HALLUCINATION_FLAG
        ]
        self.assertEqual(len(flags), 1)

    def test_allows_verified_price(self) -> None:
        self.guard.register_tool_call("tc-1")
        text = "Gold is at $2,400."
        out = self.guard.sanitize_text(text, tool_call_ref="tc-1")
        self.assertIn("$2,400", out)
