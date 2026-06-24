"""Business-type-aware valuation router tests."""
import unittest

from backend.brain.sector_multiples import build_sector_medians
from backend.brain.valuation_router import value_company


class TestSectorMultiples(unittest.TestCase):
    def test_sector_medians(self):
        med = build_sector_medians([
            {"sector": "Tech", "pe_ratio": 20, "ev_ebitda": 12, "ev_sales": 5},
            {"sector": "Tech", "pe_ratio": 30, "ev_ebitda": 16, "ev_sales": 7},
            {"sector": "Health", "pe_ratio": 15},
        ])
        self.assertEqual(med["Tech"]["pe_ratio"], 25.0)
        self.assertEqual(med["Tech"]["ev_ebitda"], 14.0)


class TestValuationRouter(unittest.TestCase):
    def test_owner_earnings_dcf_for_compounder(self):
        out = value_company({
            "fcf_per_share": 6.0,
            "fcf_growth": 0.10,
            "discount_rate": 0.09,
            "terminal_growth": 0.025,
        }, "wide_moat_compounder", current_price=100.0)
        self.assertEqual(out["status"], "ok")
        self.assertGreater(out["intrinsic_value_mid"], 100.0)
        self.assertGreater(out["margin_of_safety_base"], 0)
        self.assertTrue(any(m["method"] == "owner_earnings_dcf" for m in out["method_breakdown"]))
        self.assertIn("implied_growth", out["reverse_dcf"])

    def test_high_growth_revenue_dcf_for_unprofitable(self):
        out = value_company({
            "revenue_per_share": 15.0,
            "revenue_growth_yoy": 0.40,
            "gross_margin": 0.75,
            "target_operating_margin": 0.22,
            "discount_rate": 0.11,
        }, "high_growth_unprofitable", current_price=80.0)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(any(m["method"] == "high_growth_revenue_dcf"
                            for m in out["method_breakdown"]))

    def test_peer_multiples_requires_peers(self):
        out = value_company({
            "sector": "Tech",
            "eps_ttm": 4.0,
        }, "profitable_growth", current_price=60.0, sector_medians=None)
        # No FCF and no peer medians -> no fake value.
        self.assertEqual(out["status"], "insufficient_data")

    def test_peer_multiples_with_sector_medians(self):
        med = {"Tech": {"pe_ratio": 25.0}}
        out = value_company({
            "sector": "Tech",
            "eps_ttm": 4.0,
        }, "profitable_growth", current_price=80.0, sector_medians=med)
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["intrinsic_value_mid"], 100.0)

    def test_financial_residual_income_path(self):
        out = value_company({
            "sector": "Financials",
            "book_value_per_share": 40.0,
            "roe": 0.14,
            "cost_of_equity": 0.10,
        }, "financial", current_price=45.0)
        self.assertEqual(out["status"], "ok")
        methods = {m["method"] for m in out["method_breakdown"]}
        self.assertIn("financial_residual_income", methods)
        self.assertNotIn("owner_earnings_dcf", methods)

    def test_insufficient_data_is_truthful(self):
        out = value_company({}, "mature_cash_flow", current_price=50.0)
        self.assertEqual(out["status"], "insufficient_data")
        self.assertIsNone(out["intrinsic_value_mid"])
        self.assertEqual(out["method_breakdown"], [])


if __name__ == "__main__":
    unittest.main()
