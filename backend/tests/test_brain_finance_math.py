"""Unit tests for backend.brain.finance_math (pure, offline)."""
import math
import unittest

import numpy as np

from backend.brain import finance_math as fm


class TestFinanceMath(unittest.TestCase):
    def test_daily_returns(self):
        r = fm.daily_returns([100, 110, 99])
        self.assertEqual(len(r), 2)
        self.assertAlmostEqual(r[0], 0.10)
        self.assertAlmostEqual(r[1], -0.1, places=6)

    def test_daily_returns_too_short(self):
        self.assertEqual(fm.daily_returns([100]).size, 0)

    def test_cumulative_return(self):
        prices = list(range(1, 30))  # 1..29
        self.assertAlmostEqual(fm.cumulative_return(prices, 1), 29 / 28 - 1)
        # not enough history -> None (no fabrication)
        self.assertIsNone(fm.cumulative_return([1, 2, 3], 5))

    def test_cagr(self):
        self.assertAlmostEqual(fm.cagr(100, 200, 1), 1.0)
        self.assertAlmostEqual(fm.cagr(100, 400, 2), 1.0)
        self.assertIsNone(fm.cagr(0, 100, 1))
        self.assertIsNone(fm.cagr(100, 100, 0))

    def test_volatility_and_sharpe(self):
        rng = np.random.default_rng(0)
        rets = rng.normal(0.0005, 0.01, size=300)
        vol = fm.annualized_volatility(rets)
        self.assertTrue(0.10 < vol < 0.25)
        sharpe = fm.sharpe_ratio(rets)
        self.assertIsInstance(sharpe, float)

    def test_max_drawdown(self):
        prices = [100, 120, 60, 80]
        dd = fm.max_drawdown(prices)
        self.assertAlmostEqual(dd, 60 / 120 - 1)  # -0.5
        self.assertLessEqual(dd, 0.0)

    def test_max_drawdown_monotonic_up_is_zero(self):
        self.assertAlmostEqual(fm.max_drawdown([1, 2, 3, 4]), 0.0)

    def test_fundamentals(self):
        self.assertAlmostEqual(fm.free_cash_flow(100, -30), 70)
        self.assertAlmostEqual(fm.fcf_yield(70, 1000), 0.07)
        self.assertAlmostEqual(fm.gross_margin(40, 100), 0.4)
        self.assertIsNone(fm.gross_margin(40, 0))  # div by zero -> None
        self.assertAlmostEqual(fm.debt_to_equity(50, 200), 0.25)
        self.assertAlmostEqual(fm.enterprise_value(1000, 200, 50), 1150)

    def test_roic(self):
        val = fm.roic(operating_income=100, tax_rate=0.2, total_debt=200,
                      shareholders_equity=600, cash=100)
        self.assertAlmostEqual(val, 80 / 700)

    def test_percentile_rank(self):
        pop = [1, 2, 3, 4]
        self.assertAlmostEqual(fm.percentile_rank(3, pop), 0.75)
        self.assertIsNone(fm.percentile_rank(1, [None, float("nan")]))

    def test_none_inputs_return_none(self):
        self.assertIsNone(fm.fcf_yield(None, 1000))
        self.assertIsNone(fm.annualized_volatility([0.01]))


if __name__ == "__main__":
    unittest.main()
