"""Unit tests for owner-earnings DCF valuation inputs."""
from __future__ import annotations

import unittest

from backend.valuation_inputs import (
    build_base_growth_path,
    capm_wacc,
    compute_dcf_scenarios,
    dcf_equity_value,
    dcf_fair_value_per_share,
    net_cash_equity,
    owner_earnings_fcf,
)


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
        path = build_base_growth_path(0.166, None)
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
        }
        result = compute_dcf_scenarios(snapshot, hist_cagr_pct=None, price_usd=298.0)
        self.assertTrue(result["available"])
        base = result["scenarios"]["base"]
        bear = result["scenarios"]["bear"]
        self.assertIsNotNone(base)
        self.assertIsNotNone(bear)
        assert base is not None and bear is not None
        self.assertGreater(base, 120.0)
        self.assertLess(base, 200.0)
        self.assertGreater(bear, 110.0)
        self.assertLess(bear, base)

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
