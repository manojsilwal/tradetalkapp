"""Unit tests for UBDS v1.0 scoring."""

import unittest

from backend.eval.ubds.scoring import (
    calculate_ubds,
    grade_from_score,
    release_gate,
    score_task_success,
)
from backend.eval.ubds.schemas import CategoryScore, TaskResultRow


class TestUbdsScoring(unittest.TestCase):
    def test_grade_mapping(self) -> None:
        self.assertEqual(grade_from_score(96), "A+")
        self.assertEqual(grade_from_score(88), "B+")
        self.assertEqual(grade_from_score(55), "F")

    def test_task_success_all_complete(self) -> None:
        tasks = [
            TaskResultRow(task_id="a", completed=True, critical=True),
            TaskResultRow(task_id="b", completed=True, critical=True),
        ]
        cat = score_task_success(tasks)
        self.assertGreaterEqual(cat.score, 90.0)

    def test_release_gate_fail_on_critical_a11y(self) -> None:
        cats = {
            "task_success_completion": CategoryScore(score=90, weight=0.25),
            "accessibility_responsiveness": CategoryScore(score=90, weight=0.10),
            "error_rate_recovery": CategoryScore(score=80, weight=0.15),
        }
        self.assertEqual(release_gate(85, cats, critical_issues=1, critical_abandon_rate=0.0), "fail")

    def test_calculate_ubds_weighted(self) -> None:
        cats = {k: CategoryScore(score=80.0, weight=w) for k, w in [
            ("task_success_completion", 0.25),
            ("efficiency_flow_friction", 0.15),
            ("error_rate_recovery", 0.15),
            ("navigation_information_architecture", 0.15),
            ("visual_design_consistency", 0.10),
            ("accessibility_responsiveness", 0.10),
            ("user_satisfaction_trust", 0.10),
        ]}
        self.assertEqual(calculate_ubds(cats), 80.0)


if __name__ == "__main__":
    unittest.main()
