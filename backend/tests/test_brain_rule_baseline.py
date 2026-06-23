"""Rule-based composite scorer tests."""
import unittest

from backend.brain import SIGNAL_GROUPS
from backend.brain import rule_baseline


class TestRuleBaseline(unittest.TestCase):
    def _row(self, **over):
        base = {
            "return_3m": 0.0, "return_6m": 0.0, "return_12m": 0.0,
            "relative_strength_3m": 0.0, "price_vs_200dma": 0.0,
            "roic": 0.1, "operating_margin": 0.1, "fcf_margin": 0.1,
            "revenue_growth_yoy": 0.1, "net_margin": 0.1, "debt_to_equity": 1.0,
            "fcf_yield": 0.05, "pe_ratio": 20, "ev_ebitda": 12,
            "valuation_percentile_5y": 0.5, "capital_flow_score": 0.5,
            "institutional_accumulation_score": 0.5, "new_product_expansion_score": 0.5,
            "management_tone_score": 0.5, "filing_risk_score": 0.5, "sentiment_score": 0.0,
            "volatility_3m": 0.3, "max_drawdown_6m": -0.2, "customer_concentration_score": 0.5,
        }
        base.update(over)
        return base

    def test_scores_in_range_and_have_all_groups(self):
        rows = [self._row(), self._row(return_3m=0.5), self._row(return_3m=-0.5)]
        scored = rule_baseline.score_cross_section(rows)
        self.assertEqual(len(scored), 3)
        for s in scored:
            for g in SIGNAL_GROUPS:
                self.assertIn(g, s)
                self.assertGreaterEqual(s[g], 0.0)
                self.assertLessEqual(s[g], 100.0)
            self.assertGreaterEqual(s["composite_score"], 0.0)
            self.assertLessEqual(s["composite_score"], 100.0)

    def test_higher_momentum_scores_higher(self):
        rows = [
            self._row(return_3m=-0.4, return_6m=-0.4, return_12m=-0.4),  # weak
            self._row(return_3m=0.4, return_6m=0.4, return_12m=0.4),     # strong
        ]
        scored = rule_baseline.score_cross_section(rows)
        self.assertGreater(scored[1]["momentum"], scored[0]["momentum"])

    def test_lower_volatility_scores_higher_risk(self):
        rows = [
            self._row(volatility_3m=0.8, max_drawdown_6m=-0.6),  # risky
            self._row(volatility_3m=0.1, max_drawdown_6m=-0.05),  # calm
        ]
        scored = rule_baseline.score_cross_section(rows)
        self.assertGreater(scored[1]["risk"], scored[0]["risk"])

    def test_empty_cross_section(self):
        self.assertEqual(rule_baseline.score_cross_section([]), [])


if __name__ == "__main__":
    unittest.main()
