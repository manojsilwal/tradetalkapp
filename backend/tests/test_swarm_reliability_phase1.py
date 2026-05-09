import os
import tempfile
import unittest
from unittest import mock

from backend.swarm_reliability.artifacts import write_chat_cycle_artifacts
from backend.swarm_reliability.retrieval_fusion import fuse_and_cap_hits
from backend.swarm_reliability.stale_gate import evaluate_chat_staleness


class TestRetrievalFusionCaps(unittest.TestCase):
    def test_rrf_caps_apply_after_fusion(self):
        channels = {
            "macro_snapshots": [
                {"id": "a1", "document": "Macro A", "metadata": {"source": "fred", "extra": "x"}, "collection": "macro_snapshots"},
                {"id": "a2", "document": "Macro B", "metadata": {"source": "fred", "extra": "x"}, "collection": "macro_snapshots"},
            ],
            "debate_history": [
                {"id": "b1", "document": "Debate A", "metadata": {"source": "kb", "other": "y"}, "collection": "debate_history"},
                {"id": "b2", "document": "Debate B", "metadata": {"source": "kb", "other": "y"}, "collection": "debate_history"},
            ],
        }
        out = fuse_and_cap_hits(channels, max_records=3)
        self.assertEqual(len(out), 3)
        for row in out:
            self.assertNotIn("extra", row.get("metadata", {}))
            self.assertNotIn("other", row.get("metadata", {}))

    def test_news_depth_cap(self):
        long_doc = "x" * 5000
        channels = {
            "market_news": [
                {"id": "n1", "document": long_doc, "metadata": {"source": "feed"}, "collection": "market_news"},
            ]
        }
        out = fuse_and_cap_hits(channels, max_records=1)
        self.assertEqual(len(out), 1)
        self.assertLessEqual(len(out[0]["document"]), 700)


class TestStaleGate(unittest.TestCase):
    def test_stale_data_blocks_when_l1_too_old(self):
        now = 1_800_000_000.0
        meta = {"l1_updated_at": now - (300 * 60), "stale_session": False}
        with mock.patch("time.time", return_value=now):
            report = evaluate_chat_staleness(cycle_id="c1", meta=meta, skill_tier="SIMPLE")
        self.assertIsNotNone(report)
        self.assertEqual(report.status, "STALE_DATA")


class TestArtifacts(unittest.TestCase):
    def test_run_artifacts_written_when_env_set(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["SWARM_RUN_ARTIFACTS_DIR"] = td
            run_dir = write_chat_cycle_artifacts(
                cycle_id="cycle-1",
                meta={"session_id": "s1", "l1_updated_at": 123, "stale_session": False},
                evidence={"rag_chunk_refs": [], "status": "OK"},
                tool_trace=[],
                stale_data_report=None,
            )
            self.assertIsNotNone(run_dir)
            self.assertTrue(os.path.exists(os.path.join(run_dir, "evidence_manifest.json")))
            self.assertTrue(os.path.exists(os.path.join(run_dir, "cycle_config.json")))
            self.assertTrue(os.path.exists(os.path.join(run_dir, "final_signal.json")))
            os.environ.pop("SWARM_RUN_ARTIFACTS_DIR", None)


class TestAgentsTemplate(unittest.TestCase):
    def test_agents_guides_follow_required_template(self):
        required = [
            "## Purpose",
            "## Inputs",
            "## Allowed Tools",
            "## Forbidden Actions",
            "## Output Contract",
            "## Known Failure Modes",
            "## Evidence Requirements",
            "## Context Budget",
            "## Escalation Rules",
        ]
        targets = [
            "agents/chat_retrieval/AGENTS.md",
            "agents/chat_synthesis/AGENTS.md",
            "agents/chat_tools/AGENTS.md",
            "agents/debate/AGENTS.md",
            "agents/swarm_trace/AGENTS.md",
            "agents/gold_advisor/AGENTS.md",
            "agents/strategy_parser/AGENTS.md",
            "agents/notifications/AGENTS.md",
        ]
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        for rel in targets:
            path = os.path.join(root, rel)
            self.assertTrue(os.path.exists(path), rel)
            with open(path, "r", encoding="utf-8") as fh:
                txt = fh.read()
            for marker in required:
                self.assertIn(marker, txt, f"{rel} missing {marker}")


if __name__ == "__main__":
    unittest.main()

