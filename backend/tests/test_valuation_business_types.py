import unittest
from backend.valuation_inputs import (
    compute_dcf_scenarios,
    build_base_growth_path,
)
from backend.decision_terminal import (
    _multiples_heuristic_fair_price,
    _build_valuation_panel,
    _ResolvedSpot,
)

class TestValuationBusinessTypes(unittest.TestCase):
    def test_build_base_growth_path_relaxation(self):
        # Mature company (stable) -> caps at 15%
        mature_path = build_base_growth_path(65.0, terminal_growth=0.025, business_type="mature_cash_flow")
        self.assertAlmostEqual(mature_path[0], 0.15)
        
        # High growth company -> caps at 35%
        hg_path = build_base_growth_path(65.0, terminal_growth=0.025, business_type="profitable_growth")
        self.assertAlmostEqual(hg_path[0], 0.35)

    def test_multiples_target_pe_relaxation(self):
        # Mature company -> capped at 28x target P/E
        pe_mature = _multiples_heuristic_fair_price(
            trailing_eps=5.0,
            roe_pct=30.0,
            current_price=100.0,
            trailing_pe=20.0,
            business_type="mature_cash_flow",
            revenue_growth=0.10,
        )
        # base_pe (12.0) + roe_pct/3.0 (10.0) = 22.0. capped at 28.
        # pe_norm = 18.0 / 20.0 = 0.9. target_pe = 22.0 * 0.9 = 19.8.
        # 5.0 * 19.8 = 99.0.
        self.assertAlmostEqual(pe_mature, 99.0)

        # High growth company -> allows target P/E above 28x
        pe_hg = _multiples_heuristic_fair_price(
            trailing_eps=5.0,
            roe_pct=30.0,
            current_price=100.0,
            trailing_pe=20.0,
            business_type="profitable_growth",
            revenue_growth=0.50, # 50% revenue growth
        )
        # base_pe (12) + roe/3 (10) + growth_bonus (0.5 * 100 * 0.4 = 20) = 42.
        # pe_norm = 18 / 20 = 0.9. target_pe = 42 * 0.9 = 37.8.
        # 5.0 * 37.8 = 189.0.
        self.assertAlmostEqual(pe_hg, 189.0)

    def test_compute_dcf_scenarios_routing(self):
        # NVIDIA style: High growth + profitable -> High-Growth DCF
        snapshot_nvda = {
            "sharesOutstanding": 100_000_000,
            "marketCap": 20_000_000_000,
            "beta": 1.4,
            "totalDebt": 0,
            "totalCash": 2_000_000_000,
            "totalRevenue": 4_000_000_000,
            "revenueGrowth": 0.65,
            "grossMargins": 0.75,
            "freeCashflow": 2_400_000_000,
            "operatingCashflow": 2_500_000_000,
            "capitalExpenditures": -100_000_000,
            "returnOnEquity": 0.45,
            "debtToEquity": 10.0,
        }
        res = compute_dcf_scenarios(snapshot_nvda)
        self.assertEqual(res["model_name"], "High-Growth Revenue-to-FCF DCF")
        self.assertEqual(res["business_type"], "profitable_growth")

        # Stable / mature style -> Owner-Earnings DCF
        snapshot_stable = {
            "sharesOutstanding": 100_000_000,
            "marketCap": 10_000_000_000,
            "beta": 0.9,
            "totalDebt": 0,
            "totalCash": 500_000_000,
            "totalRevenue": 2_000_000_000,
            "revenueGrowth": 0.05,
            "grossMargins": 0.35,
            "freeCashflow": 200_000_000,
            "operatingCashflow": 250_000_000,
            "capitalExpenditures": -50_000_000,
            "returnOnEquity": 0.12,
            "debtToEquity": 30.0,
        }
        res_stable = compute_dcf_scenarios(snapshot_stable)
        self.assertEqual(res_stable["model_name"], "Mature Owner-Earnings DCF")
        self.assertEqual(res_stable["business_type"], "mature_cash_flow")

    def test_weighted_consensus_valuation_panel(self):
        # Apple style inputs
        ext = {
            "sharesOutstanding": 10_000_000,
            "marketCap": 1_000_000_000,
            "beta": 1.0,
            "totalDebt": 0,
            "totalCash": 100_000_000,
            "totalRevenue": 500_000_000,
            "revenueGrowth": 0.08,
            "grossMargins": 0.40,
            "freeCashflow": 80_000_000,
            "operatingCashflow": 90_000_000,
            "capitalExpenditures": -10_000_000,
            "returnOnEquity": 0.25,
            "trailingEps": 8.0,
        }
        resolved = _ResolvedSpot(
            price_f=100.0,
            spot_price_source="test",
            market_data_degraded=False,
            filled_spot_from_ext=False,
            spot_envelope=None,
            debate_spot_price_source=None,
        )
        panel = _build_valuation_panel(
            ticker="AAPL",
            debate_data={"roe": 25.0, "pe_ratio": 12.5},
            ext=ext,
            resolved=resolved,
            hist_cagr=0.08,
            hist_quality={},
            momentum_readout=None,
        )
        self.assertIsNotNone(panel.average_fair_value_usd)
        
        # Check that we have both DCF and Multiples models in models list
        model_names = [m.name for m in panel.models]
        self.assertIn("DCF", model_names)
        self.assertIn("Multiples", model_names)

if __name__ == "__main__":
    unittest.main()
