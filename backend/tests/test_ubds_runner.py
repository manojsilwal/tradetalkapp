"""UBDS runner and report output tests."""

import json
import tempfile
import unittest
from pathlib import Path

from backend.eval.ubds.runner import run_ubds


class TestUbdsRunner(unittest.TestCase):
    def test_fixture_run_writes_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            datasets = root / "evals" / "datasets"
            datasets.mkdir(parents=True)
            (datasets / "ubds_task_results.jsonl").write_text(
                json.dumps({
                    "task_id": "t1",
                    "task_name": "Test",
                    "completed": True,
                    "time_on_task_ms": 1000,
                    "error_count": 0,
                    "steps": 2,
                    "critical": True,
                }) + "\n",
                encoding="utf-8",
            )
            (datasets / "ubds_accessibility_fixture.json").write_text(
                json.dumps({"critical_accessibility_issues": 0, "moderate_accessibility_issues": 0}),
                encoding="utf-8",
            )
            (datasets / "ubds_visual_fixture.json").write_text("{}", encoding="utf-8")
            (datasets / "ubds_satisfaction_fixture.json").write_text("{}", encoding="utf-8")

            # Patch REPO_ROOT by running from real repo (integration) — use actual repo
        result = run_ubds(mode="fixture")
        self.assertTrue(result.run_id.startswith("uiux_eval_"))
        self.assertGreater(result.overall_ui_behavior_design_score, 0)
        summary_path = Path(__file__).resolve().parents[2] / "frontend" / "public" / "dashboard" / "uiux-summary.json"
        self.assertTrue(summary_path.is_file(), "dashboard uiux-summary.json should exist after run")


if __name__ == "__main__":
    unittest.main()
