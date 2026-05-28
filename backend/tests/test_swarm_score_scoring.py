import unittest

from backend.eval.swarm_score.scoring import (
    ablation_recommendation,
    calculate_aes,
    dashboard_badge_from_status,
    dashboard_status_from_gates,
    latency_score_from_p95,
)
from backend.eval.swarm_score.schemas import CategoryScores


class TestSwarmScoreScoring(unittest.TestCase):
    def test_calculate_aes_weighting(self):
        scores = CategoryScores(
            task_success=80,
            rag_quality=90,
            orchestration_effectiveness=70,
            continual_learning_value=60,
            efficiency=75,
            safety_groundedness=95,
            maintainability=85,
        )
        aes = calculate_aes(scores)
        self.assertAlmostEqual(aes, 78.5, places=2)

    def test_latency_score_bands(self):
        self.assertEqual(latency_score_from_p95(1800), 100.0)
        self.assertEqual(latency_score_from_p95(3800), 90.0)
        self.assertEqual(latency_score_from_p95(5900), 80.0)
        self.assertEqual(latency_score_from_p95(7900), 70.0)
        self.assertEqual(latency_score_from_p95(11900), 55.0)
        self.assertEqual(latency_score_from_p95(13000), 35.0)

    def test_dashboard_fail_gate(self):
        status = dashboard_status_from_gates(
            critical_hallucinations=1,
            fabricated_tool_call_claims=0,
            tool_call_validity=0.99,
            citation_validity=0.95,
            risky_component_changed=False,
            candidate_beats_production=True,
        )
        self.assertEqual(status, "fail")
        badge = dashboard_badge_from_status(status)
        self.assertEqual(badge.color, "red")

    def test_ablation_recommendation(self):
        self.assertEqual(ablation_recommendation(5.0), "Keep always-on")
        self.assertEqual(ablation_recommendation(3.0), "Make conditional")
        self.assertEqual(ablation_recommendation(1.0), "Disable by default")
        self.assertEqual(ablation_recommendation(-0.1), "Remove or redesign")


if __name__ == "__main__":
    unittest.main()

