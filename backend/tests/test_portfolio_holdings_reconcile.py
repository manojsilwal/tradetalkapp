"""Unit tests for paper portfolio holdings import reconciliation."""
import unittest

from backend.portfolio_holdings_reconcile import (
    aggregate_open_long_positions,
    normalize_extracted_holdings,
    reconcile_holdings,
)


class TestPortfolioHoldingsReconcile(unittest.TestCase):
    def test_normalize_dedupes_tickers(self):
        rows = [
            {"ticker": "aapl", "shares": 1},
            {"ticker": "AAPL", "shares": 2},
            {"ticker": "MSFT", "avg_cost": 100},
        ]
        out = normalize_extracted_holdings(rows)
        self.assertEqual([r["ticker"] for r in out], ["AAPL", "MSFT"])

    def test_aggregate_vwap(self):
        positions = [
            {
                "id": "1",
                "ticker": "AAPL",
                "direction": "LONG",
                "closed": 0,
                "shares": 10,
                "allocated": 1000,
            },
            {
                "id": "2",
                "ticker": "AAPL",
                "direction": "LONG",
                "closed": 0,
                "shares": 10,
                "allocated": 1200,
            },
            {
                "id": "3",
                "ticker": "TSLA",
                "direction": "SHORT",
                "closed": 0,
                "shares": 5,
                "allocated": 500,
            },
        ]
        agg = aggregate_open_long_positions(positions)
        self.assertIn("AAPL", agg)
        self.assertNotIn("TSLA", agg)
        self.assertAlmostEqual(agg["AAPL"]["shares"], 20.0)
        self.assertAlmostEqual(agg["AAPL"]["avg_cost"], 110.0)

    def test_reconcile_new_updated_unchanged_removed(self):
        current = {
            "AAPL": {
                "shares": 10,
                "avg_cost": 100,
                "allocated": 1000,
                "position_ids": ["a"],
            },
            "MSFT": {
                "shares": 2,
                "avg_cost": 300,
                "allocated": 600,
                "position_ids": ["b"],
            },
            "XOM": {
                "shares": 4,
                "avg_cost": 50,
                "allocated": 200,
                "position_ids": ["c"],
            },
        }
        extracted = normalize_extracted_holdings(
            [
                {"ticker": "AAPL", "shares": 10, "avg_cost": 100},
                {"ticker": "MSFT", "shares": 2, "avg_cost": 310},
                {"ticker": "GOOG", "shares": 5, "avg_cost": 140},
            ]
        )
        r = reconcile_holdings(extracted, current, full_snapshot=True)
        self.assertEqual(len(r["new"]), 1)
        self.assertEqual(r["new"][0]["ticker"], "GOOG")
        self.assertEqual(len(r["updated"]), 1)
        self.assertEqual(r["updated"][0]["ticker"], "MSFT")
        self.assertEqual(len(r["unchanged"]), 1)
        self.assertEqual(r["unchanged"][0]["ticker"], "AAPL")
        self.assertEqual(len(r["removed"]), 1)
        self.assertEqual(r["removed"][0]["ticker"], "XOM")

    def test_partial_snapshot_no_removed(self):
        current = {
            "AAPL": {
                "shares": 1,
                "avg_cost": 1,
                "allocated": 1,
                "position_ids": ["a"],
            },
        }
        extracted = normalize_extracted_holdings([{"ticker": "MSFT", "shares": 2, "avg_cost": 300}])
        r = reconcile_holdings(extracted, current, full_snapshot=False)
        self.assertEqual(r["removed"], [])


if __name__ == "__main__":
    unittest.main()
