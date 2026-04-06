"""CORAL structured hub (SQLite) and heartbeat helpers."""
import asyncio
import os
import unittest
from unittest.mock import patch

from backend import coral_hub
from backend.coral_heartbeat import _intel_one_liner
from backend import slo_targets


class TestCoralHub(unittest.TestCase):
    def setUp(self):
        self._orig = coral_hub.DB_PATH
        coral_hub.reset_thread_local_connection()

    def tearDown(self):
        coral_hub.DB_PATH = self._orig
        coral_hub.reset_thread_local_connection()

    def test_hub_notes_and_skills(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            coral_hub.DB_PATH = path
            coral_hub.init_coral_hub_db()
            coral_hub.add_note("test_agent", "observation about regime", market_regime="BULL_NORMAL")
            coral_hub.add_skill("rsi_rule", "When RSI>70 and VIX spikes, trim risk.", contributed_by="test_agent")
            notes = coral_hub.list_recent_notes(n=5)
            self.assertGreaterEqual(len(notes), 1)
            self.assertIn("observation", notes[0]["observation"])
            block = coral_hub.format_hub_context_block(market_regime="BULL_NORMAL")
            self.assertIn("CORAL hub", block)
            self.assertIn("rsi_rule", block)
        finally:
            os.unlink(path)

    def test_record_attempt(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            coral_hub.DB_PATH = path
            coral_hub.init_coral_hub_db()
            coral_hub.record_attempt("task_a", "agent_x", 1.0, 0.75)
            conn = coral_hub._conn()
            n = conn.execute("SELECT COUNT(*) FROM coral_attempts").fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            os.unlink(path)


class TestCoralHeartbeat(unittest.TestCase):
    def test_intel_one_liner(self):
        intel = {
            "headlines": ["Fed signals patience on rates"],
            "fomc": {"next_meeting": "2026-04-29"},
            "sector_perf": {"XLK": {"name": "Tech", "pct": 1.2}},
        }
        s = _intel_one_liner(intel)
        self.assertIn("Headline", s)
        self.assertIn("FOMC", s)


class TestSloTargets(unittest.TestCase):
    def test_constants(self):
        self.assertGreater(slo_targets.CHAT_P95_LATENCY_MS_TARGET, 0)
        self.assertEqual(slo_targets.CHAT_RAG_COLLECTION_QUERIES_PER_MESSAGE, 4)
        self.assertGreater(slo_targets.SWARM_TRACE_P95_SECONDS_TARGET, 0)


class TestHeartbeatAsync(unittest.TestCase):
    def test_run_skipped_when_disabled(self):
        from backend.coral_heartbeat import run_coral_heartbeat

        async def _run():
            with patch.dict(os.environ, {"CORAL_HEARTBEAT_ENABLED": "0"}):
                return await run_coral_heartbeat(None)

        out = asyncio.run(_run())
        self.assertTrue(out.get("skipped"))


if __name__ == "__main__":
    unittest.main()
