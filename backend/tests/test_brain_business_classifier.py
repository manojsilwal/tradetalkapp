"""Business classifier tests (offline)."""
import unittest

from backend.brain.business_classifier import classify_business


class TestBusinessClassifier(unittest.TestCase):
    def test_high_growth_unprofitable(self):
        c = classify_business({
            "revenue_growth_yoy": 0.45,
            "fcf_margin": -0.20,
            "gross_margin": 0.75,
            "debt_to_equity": 0.2,
        })
        self.assertEqual(c["business_type"], "high_growth_unprofitable")
        self.assertGreater(c["type_scores"]["high_growth_unprofitable"], 0.5)

    def test_wide_moat_compounder(self):
        c = classify_business({
            "market_cap": 300e9,
            "revenue_growth_yoy": 0.10,
            "fcf_margin": 0.28,
            "operating_margin": 0.32,
            "roic": 0.30,
            "revenue_volatility": 0.03,
        })
        self.assertEqual(c["business_type"], "wide_moat_compounder")

    def test_financial_by_sector(self):
        c = classify_business({"sector": "Financials", "fcf_margin": 0.30, "roic": 0.20})
        self.assertEqual(c["business_type"], "financial")
        self.assertEqual(c["type_scores"]["financial"], 1.0)

    def test_hysteresis_keeps_prior_type_when_close(self):
        c = classify_business({
            "market_cap": 120e9,
            "revenue_growth_yoy": 0.14,
            "fcf_margin": 0.12,
            "operating_margin": 0.20,
            "roic": 0.16,
            "revenue_volatility": 0.05,
        }, prior_type="mature_cash_flow", hysteresis_margin=1.0)
        self.assertEqual(c["business_type"], "mature_cash_flow")
        self.assertTrue(any("hysteresis" in r for r in c["classification_reason"]))


if __name__ == "__main__":
    unittest.main()
