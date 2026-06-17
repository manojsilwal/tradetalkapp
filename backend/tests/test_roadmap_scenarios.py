"""Roadmap scenario sanitization and extrapolation caps."""
import unittest

from backend.decision_terminal import _sanitize_roadmap_scenarios, _heuristic_roadmap
from backend.predictor.scenarios import extrapolate_geometric_3y


class TestRoadmapSanitize(unittest.TestCase):
    def test_misscaled_llm_prices_discarded_not_reanchored(self):
        spot = 315.0
        bull, base, bear = _sanitize_roadmap_scenarios(spot, 42.0, 20.0, 9.0)
        self.assertIsNone(bull)
        self.assertIsNone(base)
        self.assertIsNone(bear)

    def test_negative_cagr_heuristic_discarded_when_misscaled(self):
        u, b, e, cagr, asm = _heuristic_roadmap(100.0, hist_cagr_3y=-25.0)
        self.assertIsNone(u)
        self.assertIsNone(b)
        self.assertIsNone(e)
        self.assertIsNone(cagr)
        self.assertTrue(asm)

    def test_predictor_extrapolation_capped(self):
        spot = 200.0
        wild = extrapolate_geometric_3y(spot, 63, spot * 4.0)
        self.assertIsNotNone(wild)
        self.assertLessEqual(wild, spot * 2.75)
        self.assertGreaterEqual(wild, spot * 0.35)


if __name__ == "__main__":
    unittest.main()
