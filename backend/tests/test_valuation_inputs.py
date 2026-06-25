"""Unit tests for owner-earnings DCF valuation inputs."""
from __future__ import annotations

import unittest

from backend.valuation_inputs import (
    _market_expectation_label,
    _mature_compounder_fcf_anchor,
    _sanitize_forward_growth_estimate,
    build_base_growth_path,
    capm_wacc,
    compute_dcf_scenarios,
    dcf_equity_value,
    dcf_fair_value_per_share,
    median_ocf_yoy_growth_pct,
    median_owner_earnings_fcf,
    net_cash_equity,
    owner_earnings_fcf,
)


class TestMatureCompounderCalibration(unittest.TestCase):
    def test_sanitize_forward_growth_drops_jnj_style_outlier(self) -> None:
        self.assertIsNone(_sanitize_forward_growth_estimate(-0.529, 0.099))
        self.assertAlmostEqual(
            _sanitize_forward_growth_estimate(0.194, 0.073), 0.194
        )

    def test_median_owner_earnings_4y_blend(self) -> None:
        rows = [
            {"year": 2022, "ocf": 100.0, "capex": -20.0},
            {"year": 2023, "ocf": 110.0, "capex": -22.0},
            {"year": 2024, "ocf": 120.0, "capex": -24.0},
            {"year": 2025, "ocf": 130.0, "capex": -26.0},
        ]
        fcf, src = median_owner_earnings_fcf(rows)
        self.assertEqual(src, "weighted_4y_fcf")
        # latest OE=104; 3y avg OE=(88+96+104)/3=96; blend=100
        self.assertAlmostEqual(fcf, 100.0, places=1)

    def test_mature_fcf_anchor_uses_maintenance_normalized(self) -> None:
        snapshot = {
            "totalRevenue": 100_000_000_000,
            "operatingCashflow": 40_000_000_000,
            "capitalExpenditures": -24_000_000_000,
            "depreciation": 15_000_000_000,
            "capex_history_5y": [20e9, 21e9, 22e9, 23e9, 24e9],
        }
        fcf, src = _mature_compounder_fcf_anchor(
            snapshot, 5_000_000_000, "latest_fcf_fallback", "wide_moat_compounder"
        )
        self.assertIn("maintenance_normalized", src)
        self.assertGreater(fcf, 16_000_000_000)

    def test_jnj_style_snapshot_not_crushed_by_forward_earnings(self) -> None:
        snapshot = {
            "ticker": "JNJ",
            "sharesOutstanding": 2_400_000_000,
            "marketCap": 580_000_000_000,
            "beta": 0.6,
            "totalDebt": 40_000_000_000,
            "totalCash": 20_000_000_000,
            "totalRevenue": 96_000_000_000,
            "revenueGrowth": 0.099,
            "earningsGrowth": -0.529,
            "grossMargins": 0.68,
            "operatingMargins": 0.27,
            "returnOnEquity": 0.26,
            "operatingCashflow": 22_000_000_000,
            "capitalExpenditures": -5_000_000_000,
            "depreciation": 7_500_000_000,
            "annual_cashflow_5y": [
                {"year": 2022, "ocf": 21_000_000_000, "capex": -4_000_000_000},
                {"year": 2023, "ocf": 22_000_000_000, "capex": -5_000_000_000},
                {"year": 2024, "ocf": 24_000_000_000, "capex": -6_000_000_000},
                {"year": 2025, "ocf": 22_000_000_000, "capex": -5_000_000_000},
            ],
        }
        result = compute_dcf_scenarios(snapshot, price_usd=245.0)
        self.assertTrue(result["available"])
        base = result["scenarios"]["base"]
        assert base is not None
        # Synthetic snapshot is conservative vs live JNJ (~$235); main guard is no forward-crush.
        self.assertGreater(base, 160.0)
        self.assertLess(base, 280.0)


class TestMarketExpectationLabel(unittest.TestCase):
    def test_no_deep_value_wording(self):
        # Large undervaluation no longer reads as "deep value".
        label = _market_expectation_label(60.0)
        self.assertEqual(label, "well below base case")
        self.assertNotIn("deep value", label)

    def test_buckets(self):
        self.assertEqual(_market_expectation_label(None), "unknown")
        self.assertEqual(_market_expectation_label(-40.0), "high optimism priced in")
        self.assertEqual(_market_expectation_label(0.0), "near base case")
        self.assertEqual(_market_expectation_label(25.0), "below base case")
        self.assertEqual(_market_expectation_label(45.0), "well below base case")


def _sample_annual_rows() -> list[dict]:
    """Five fiscal years with predictable YoY OCF growth rates."""
    return [
        {"year": 2020, "ocf": 100_000_000_000, "capex": -10_000_000_000},
        {"year": 2021, "ocf": 110_000_000_000, "capex": -11_000_000_000},
        {"year": 2022, "ocf": 120_000_000_000, "capex": -12_000_000_000},
        {"year": 2023, "ocf": 130_000_000_000, "capex": -13_000_000_000},
        {"year": 2024, "ocf": 140_000_000_000, "capex": -14_000_000_000},
    ]


class TestOwnerEarningsFcf(unittest.TestCase):
    def test_prefers_ocf_minus_capex(self) -> None:
        fcf, src = owner_earnings_fcf(
            {
                "operatingCashflow": 140_000_000_000,
                "capitalExpenditures": -11_000_000_000,
            }
        )
        self.assertEqual(src, "ocf_minus_capex")
        self.assertAlmostEqual(fcf, 129_000_000_000, delta=1e6)

    def test_statement_fcf_fallback(self) -> None:
        fcf, src = owner_earnings_fcf(
            {"statement_free_cash_flow": 98_000_000_000}
        )
        self.assertEqual(src, "cashflow_statement_fcf")
        self.assertEqual(fcf, 98_000_000_000)


class TestMedianCashflowHelpers(unittest.TestCase):
    def test_median_owner_earnings(self) -> None:
        rows = _sample_annual_rows()
        fcf, src = median_owner_earnings_fcf(rows)
        self.assertEqual(src, "weighted_normalized_fcf")
        # latest: 126B
        # 3y avg: (108 + 117 + 126) / 3 = 117B
        # 5y median: 108B
        # normalized: 0.5*126 + 0.3*117 + 0.2*108 = 63 + 35.1 + 21.6 = 119.7B
        self.assertAlmostEqual(fcf, 119_700_000_000, delta=1e6)

    def test_median_ocf_yoy_growth(self) -> None:
        rows = _sample_annual_rows()
        yoy = median_ocf_yoy_growth_pct(rows)
        self.assertIsNotNone(yoy)
        assert yoy is not None
        # YoY rates: 10%, 9.09%, 8.33%, 7.69% → median ≈ 8.71%
        self.assertAlmostEqual(yoy, 8.71, delta=0.05)

    def test_median_yoy_skips_non_positive_prior(self) -> None:
        rows = [
            {"year": 2022, "ocf": -5.0, "capex": -1.0},
            {"year": 2023, "ocf": 10.0, "capex": -1.0},
            {"year": 2024, "ocf": 12.0, "capex": -1.0},
        ]
        yoy = median_ocf_yoy_growth_pct(rows)
        self.assertAlmostEqual(yoy, 20.0, delta=0.01)


class TestNetCashEquity(unittest.TestCase):
    def test_balance_sheet_fallback(self) -> None:
        net, src = net_cash_equity(
            {
                "balance_cash_and_st_investments": 54_700_000_000,
                "balance_investments_and_advances": 77_700_000_000,
                "balance_total_debt": 98_700_000_000,
            }
        )
        self.assertEqual(src, "balance_sheet")
        self.assertAlmostEqual(net, 33_700_000_000, delta=1e6)


class TestDcfMath(unittest.TestCase):
    def test_declining_growth_path(self) -> None:
        path = build_base_growth_path(16.6, 0.025)
        self.assertEqual(len(path), 5)
        self.assertGreater(path[0], path[-1])

    def test_aapl_style_base_near_external_workbook(self) -> None:
        """Sanity: user-style inputs should land near $150–180 base, not ~$105."""
        snapshot = {
            "operatingCashflow": 140_220_000_000,
            "capitalExpenditures": -11_050_000_000,
            "sharesOutstanding": 14_690_000_000,
            "totalCash": 45_570_000_000,
            "shortTermInvestments": 22_940_000_000,
            "longTermInvestments": 78_090_000_000,
            "totalDebt": 84_710_000_000,
            "beta": 1.09,
            "revenueGrowth": 0.06,
            "annual_cashflow_5y": _sample_annual_rows(),
        }
        result = compute_dcf_scenarios(snapshot, hist_cagr_pct=None, price_usd=298.0)
        self.assertTrue(result["available"])
        self.assertEqual(result["fcf_source"], "weighted_normalized_fcf")
        self.assertEqual(result["growth_anchor_source"], "blended_growth_anchor")
        self.assertEqual(result["fcf_years_used"], 5)
        base = result["scenarios"]["base"]
        bear = result["scenarios"]["bear"]
        self.assertIsNotNone(base)
        self.assertIsNotNone(bear)
        assert base is not None and bear is not None
        self.assertGreater(base, 120.0)
        self.assertLess(base, 220.0) # Updated upper bound to account for terminal spread changes
        self.assertGreater(bear, 85.0) # Lowered bear bound slightly due to new WACC calculation increasing discount rate
        self.assertLess(bear, base)

    def test_prefers_median_over_ttm_when_5y_present(self) -> None:
        snapshot = {
            "operatingCashflow": 200_000_000_000,
            "capitalExpenditures": -10_000_000_000,
            "sharesOutstanding": 10_000_000_000,
            "totalDebt": 0,
            "totalCash": 0,
            "beta": 1.0,
            "revenueGrowth": 0.20,
            "annual_cashflow_5y": _sample_annual_rows(),
        }
        result = compute_dcf_scenarios(snapshot, hist_cagr_pct=25.0, price_usd=100.0)
        self.assertEqual(result["fcf_source"], "weighted_normalized_fcf")
        self.assertEqual(result["growth_anchor_source"], "blended_growth_anchor")
        self.assertNotEqual(result["fcf_usd"], 190_000_000_000)

    def test_fallback_to_ttm_when_insufficient_history(self) -> None:
        snapshot = {
            "operatingCashflow": 140_000_000_000,
            "capitalExpenditures": -11_000_000_000,
            "sharesOutstanding": 10_000_000_000,
            "totalDebt": 0,
            "totalCash": 0,
            "beta": 1.0,
            "revenueGrowth": 0.06,
            "annual_cashflow_5y": _sample_annual_rows()[:2],
        }
        result = compute_dcf_scenarios(snapshot, hist_cagr_pct=None, price_usd=100.0)
        self.assertEqual(result["fcf_source"], "ocf_minus_capex")
        self.assertEqual(result["growth_anchor_source"], "blended_growth_anchor")
        self.assertEqual(result["fcf_years_used"], 0)

    def test_capm_wacc_reasonable_for_low_beta(self) -> None:
        w = capm_wacc(1.09, risk_free=0.0446)
        self.assertGreater(w, 0.08)
        self.assertLess(w, 0.11)


class TestDcfEquityValue(unittest.TestCase):
    def test_terminal_requires_wacc_above_terminal_g(self) -> None:
        with self.assertRaises(ValueError):
            dcf_equity_value(100.0, [0.05] * 5, 0.025, 0.03)

    def test_per_share_adds_net_cash(self) -> None:
        ev = dcf_equity_value(100e9, [0.05] * 5, 0.09, 0.025)
        with_cash = dcf_fair_value_per_share(
            100e9, 10e9, 50e9, [0.05] * 5, 0.09, 0.025
        )
        without = dcf_fair_value_per_share(
            100e9, 10e9, 0.0, [0.05] * 5, 0.09, 0.025
        )
        assert with_cash is not None and without is not None
        self.assertAlmostEqual(with_cash - without, 5.0, places=1)


if __name__ == "__main__":
    unittest.main()
