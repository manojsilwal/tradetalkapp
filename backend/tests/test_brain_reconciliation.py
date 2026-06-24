"""Value-vs-price reconciliation tests."""
import unittest

from backend.brain.reconciliation import reconcile_value_price


class TestReconciliation(unittest.TestCase):
    def test_compounder_with_momentum(self):
        out = reconcile_value_price(
            {"status": "ok", "margin_of_safety_base": 0.25, "valuation_score": 75},
            {"momentum_score": 80},
            risk_score=0.25,
        )
        self.assertEqual(out["quadrant"], "compounder_with_momentum")
        self.assertEqual(out["verdict_bias"], "constructive")

    def test_undervalued_without_pricing_confirmation(self):
        out = reconcile_value_price(
            {"status": "ok", "margin_of_safety_base": 0.25, "valuation_score": 75},
            {"momentum_score": 45},
        )
        self.assertEqual(out["quadrant"], "undervalued_opportunity")

    def test_overvalued_hype(self):
        out = reconcile_value_price(
            {"status": "ok", "margin_of_safety_base": -0.30, "valuation_score": 20},
            {"momentum_score": 85},
        )
        self.assertEqual(out["quadrant"], "overvalued_hype")

    def test_high_risk_caps_setup(self):
        out = reconcile_value_price(
            {"status": "ok", "margin_of_safety_base": 0.10, "valuation_score": 60},
            {"momentum_score": 70},
            risk_score=0.80,
        )
        self.assertEqual(out["quadrant"], "speculative_volatile")
        self.assertEqual(out["verdict_bias"], "watch")


if __name__ == "__main__":
    unittest.main()
