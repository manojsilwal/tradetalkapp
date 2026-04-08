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

    def test_handoff_events_roundtrip(self):
        import tempfile
        import time

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            coral_hub.DB_PATH = path
            coral_hub.init_coral_hub_db()
            t0 = time.time() - 10
            coral_hub.log_handoff_event("handoff_swarm_trace", {"ticker": "AAPL", "global_signal": 1})
            rows = coral_hub.list_handoff_events_since(t0)
            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "handoff_swarm_trace")
            self.assertEqual(rows[0]["payload"].get("ticker"), "AAPL")
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


class TestCoralAgentReflections(unittest.TestCase):
    """Multi-agent CORAL notes (data_ingest, technical, sentiment, gold_analysis)."""

    def setUp(self):
        self._orig = coral_hub.DB_PATH
        coral_hub.reset_thread_local_connection()

    def tearDown(self):
        coral_hub.DB_PATH = self._orig
        coral_hub.reset_thread_local_connection()

    def test_agent_reflections_write_four_notes(self):
        import tempfile

        from backend.coral_agents import FINANCE_AGENT_IDS
        from backend.coral_heartbeat import run_coral_agent_reflections

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            coral_hub.DB_PATH = path
            coral_hub.init_coral_hub_db()

            async def _run():
                with patch.dict(
                    os.environ,
                    {
                        "CORAL_AGENT_REFLECTIONS": "1",
                        "CORAL_HEARTBEAT_IGNORE_MARKET_HOURS": "1",
                    },
                ):
                    return await run_coral_agent_reflections(None)

            with patch("backend.market_intel.get_intel", return_value={"headlines": ["Fed holds rates steady"]}):
                with patch(
                    "backend.market_l1_cache.get_snapshot",
                    return_value={
                        "quotes": {"SPY": 100.0, "QQQ": 200.0, "GLD": 300.0, "UUP": 28.0},
                        "vix_level": 14.5,
                        "credit_stress_index": 1.0,
                        "sector_etfs": {"XLK": 1.0},
                    },
                ):
                    out = asyncio.run(_run())

            self.assertFalse(out.get("skipped"), msg=out)
            self.assertEqual(len(out.get("note_ids", [])), 4)
            ids = {x["agent_id"] for x in out["note_ids"]}
            self.assertTrue(FINANCE_AGENT_IDS.issubset(ids))
        finally:
            os.unlink(path)

    def test_agent_reflections_respects_disable_env(self):
        from backend.coral_heartbeat import run_coral_agent_reflections

        async def _run():
            with patch.dict(os.environ, {"CORAL_AGENT_REFLECTIONS": "0"}):
                return await run_coral_agent_reflections(None)

        out = asyncio.run(_run())
        self.assertTrue(out.get("skipped"))
        self.assertIn("disabled", (out.get("reason") or "").lower())


if __name__ == "__main__":
    unittest.main()
