"""Backtest tests: ranking value, cost sensitivity, metric shape."""
import unittest

import numpy as np

from backend.brain import backtest


class TestBacktest(unittest.TestCase):
    def _panel(self, n_periods=8, n=20, seed=0):
        rng = np.random.default_rng(seed)
        dates, scores, excess, tickers = [], [], [], []
        for p in range(n_periods):
            sc = rng.normal(size=n)
            # forward excess is correlated with the score -> ranking has value
            ex = 0.03 * sc + rng.normal(0, 0.01, size=n)
            dates.extend([p] * n)
            scores.extend(sc.tolist())
            excess.extend(ex.tolist())
            tickers.extend([f"T{i}" for i in range(n)])
        return dates, scores, excess, tickers

    def test_metrics_present(self):
        d, s, e, t = self._panel()
        res = backtest.run_backtest(d, s, e, t, top_n=5, cost_bps=10)
        for k in ("sharpe", "max_drawdown", "hit_rate", "annualized_excess",
                  "avg_turnover", "final_equity", "equity_curve", "n_periods"):
            self.assertIn(k, res)
        self.assertEqual(res["n_periods"], 8)
        self.assertLessEqual(res["max_drawdown"], 0.0)

    def test_good_ranking_is_profitable(self):
        d, s, e, t = self._panel()
        res = backtest.run_backtest(d, s, e, t, top_n=5, cost_bps=5)
        self.assertGreater(res["annualized_excess"], 0.0)
        self.assertGreater(res["hit_rate"], 0.5)

    def test_costs_reduce_returns(self):
        d, s, e, t = self._panel()
        cheap = backtest.run_backtest(d, s, e, t, top_n=5, cost_bps=1)
        pricey = backtest.run_backtest(d, s, e, t, top_n=5, cost_bps=500)
        self.assertGreater(cheap["annualized_excess"], pricey["annualized_excess"])

    def test_empty(self):
        res = backtest.run_backtest([], [], [], [], top_n=5)
        self.assertEqual(res["n_periods"], 0)


if __name__ == "__main__":
    unittest.main()
