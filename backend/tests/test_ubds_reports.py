"""UBDS report writers — historical comparison."""

import json
import tempfile
import unittest
from pathlib import Path

from backend.eval.ubds.reports import _previous_overall_score, write_ubds_reports
from backend.eval.ubds.runner import run_ubds
from backend.eval.ubds.schemas import CategoryScore, UbdsRunResult


class TestUbdsReports(unittest.TestCase):
    def test_previous_score_from_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hist_dir = root / "frontend" / "public" / "dashboard"
            hist_dir.mkdir(parents=True)
            hist_path = hist_dir / "uiux-history.json"
            hist_path.write_text(
                json.dumps(
                    [
                        {"run_id": "a", "overall_score": 80.0},
                        {"run_id": "b", "overall_score": 85.0},
                    ]
                ),
                encoding="utf-8",
            )
            prev = _previous_overall_score(root)
            self.assertEqual(prev, 80.0)

    def test_write_sets_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            datasets = root / "evals" / "datasets"
            datasets.mkdir(parents=True)
            (datasets / "ubds_task_results.jsonl").write_text(
                json.dumps(
                    {
                        "task_id": "t1",
                        "task_name": "T",
                        "completed": True,
                        "time_on_task_ms": 1000,
                        "critical": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            for name in (
                "ubds_accessibility_fixture.json",
                "ubds_visual_fixture.json",
                "ubds_satisfaction_fixture.json",
            ):
                (datasets / name).write_text("{}", encoding="utf-8")

            hist_dir = root / "frontend" / "public" / "dashboard"
            hist_dir.mkdir(parents=True)
            (hist_dir / "uiux-history.json").write_text(
                json.dumps(
                    [
                        {"run_id": "old", "overall_score": 70.0},
                        {"run_id": "mid", "overall_score": 75.0},
                    ]
                ),
                encoding="utf-8",
            )

            result = run_ubds(mode="fixture", repo_root=root)
            self.assertIsNotNone(result.previous_overall_score)
            self.assertIsNotNone(result.overall_score_delta)


if __name__ == "__main__":
    unittest.main()
