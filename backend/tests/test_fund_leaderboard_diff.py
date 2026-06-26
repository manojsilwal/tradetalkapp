"""Offline tests for the quarter diff engine."""
import unittest

from backend.fund_leaderboard_diff import compute_diff


def _h(ticker, mv, shares, sector="Tech", cusip=None, issuer=None):
    return {
        "ticker": ticker, "market_value_usd": mv, "shares": shares,
        "sector": sector, "cusip": cusip, "issuer_name": issuer or ticker,
    }


class DiffEngineTest(unittest.TestCase):
    def test_first_period_has_no_prior(self):
        cur = [_h("AAPL", 600, 10), _h("MSFT", 400, 5)]
        d = compute_diff(cur, None, "2024-03-31")
        self.assertEqual(d["holdings_count"], 2)
        self.assertEqual(d["new_count"], 2)
        self.assertEqual(d["soldout_count"], 0)
        self.assertIsNone(d["turnover_estimate_pct"])
        self.assertAlmostEqual(d["total_13f_value_usd"], 1000.0)

    def test_changes_classification(self):
        prev = [_h("AAPL", 600, 10), _h("MSFT", 400, 5), _h("XOM", 200, 4)]
        cur = [
            _h("AAPL", 700, 12),   # increased shares 10 -> 12
            _h("MSFT", 300, 3),    # decreased shares 5 -> 3
            _h("NVDA", 500, 8),    # new
            # XOM sold out
        ]
        d = compute_diff(cur, prev, "2024-06-30", "2024-03-31")
        self.assertEqual(d["new_count"], 1)
        self.assertEqual(d["increased_count"], 1)
        self.assertEqual(d["decreased_count"], 1)
        self.assertEqual(d["soldout_count"], 1)
        tickers_new = [c["ticker"] for c in d["changes"]["new"]]
        self.assertIn("NVDA", tickers_new)
        self.assertIsNotNone(d["turnover_estimate_pct"])
        self.assertGreater(d["turnover_estimate_pct"], 0)

    def test_concentration(self):
        cur = [_h(f"T{i}", mv, 1) for i, mv in enumerate([500, 300, 100, 50, 50])]
        d = compute_diff(cur, None, "2024-12-31")
        # top10 == all here -> 100%
        self.assertAlmostEqual(d["top10_concentration"], 1.0, places=5)

    def test_sector_flow(self):
        prev = [_h("AAPL", 600, 10, sector="Tech"), _h("XOM", 400, 4, sector="Energy")]
        cur = [_h("AAPL", 800, 12, sector="Tech"), _h("XOM", 100, 1, sector="Energy")]
        d = compute_diff(cur, prev, "2024-06-30", "2024-03-31")
        flow = {s["sector"]: s["netFlowUsd"] for s in d["sector_flow"]}
        self.assertAlmostEqual(flow["Tech"], 200.0)
        self.assertAlmostEqual(flow["Energy"], -300.0)


if __name__ == "__main__":
    unittest.main()
