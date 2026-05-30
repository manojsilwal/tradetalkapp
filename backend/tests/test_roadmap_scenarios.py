"""Roadmap scenario sanitization and extrapolation caps."""
import unittest

from backend.decision_terminal import _sanitize_roadmap_scenarios, _heuristic_roadmap
from backend.predictor.scenarios import extrapolate_geometric_3y


class TestRoadmapSanitize(unittest.TestCase):
    def test_misscaled_llm_prices_reanchor_bull_above_spot(self):
        spot = 315.0
        bull, base, bear = _sanitize_roadmap_scenarios(spot, 42.0, 20.0, 9.0)
        self.assertIsNotNone(bull)
        self.assertGreaterEqual(bull, spot * 1.08)
        self.assertLessEqual(bear, spot * 0.92)
        self.assertGreaterEqual(bull, base)
        self.assertGreaterEqual(base, bear)

    def test_negative_cagr_heuristic_bull_not_below_spot(self):
        u, b, e, _cagr, _asm = _heuristic_roadmap(100.0, hist_cagr_3y=-25.0)
        self.assertGreaterEqual(u, 100.0 * 1.08)
        self.assertLessEqual(e, 100.0 * 0.92)
        self.assertGreaterEqual(u, b)
        self.assertGreaterEqual(b, e)

    def test_predictor_extrapolation_capped(self):
        spot = 200.0
        wild = extrapolate_geometric_3y(spot, 63, spot * 4.0)
        self.assertIsNotNone(wild)
        self.assertLessEqual(wild, spot * 2.75)
        self.assertGreaterEqual(wild, spot * 0.35)


if __name__ == "__main__":
    unittest.main()
