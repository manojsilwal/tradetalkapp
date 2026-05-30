"""Offline tests for portfolio position categorization."""
import os
import tempfile
import unittest
from unittest.mock import patch


class TestPaperPortfolioCategories(unittest.TestCase):
    def setUp(self):
        from backend import paper_portfolio as pp

        self.pp = pp
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = pp.DB_PATH
        pp.DB_PATH = os.path.join(self.tmp.name, "progress.db")
        if hasattr(pp._local, "conn"):
            pp._local.conn.close()
            delattr(pp._local, "conn")
        pp.init_portfolio_db()

    def tearDown(self):
        if hasattr(self.pp._local, "conn"):
            self.pp._local.conn.close()
            delattr(self.pp._local, "conn")
        self.pp.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_market_cap_bucket_thresholds(self):
        self.assertEqual(self.pp._classify_market_cap(250_000_000_000), "Mega Cap")
        self.assertEqual(self.pp._classify_market_cap(50_000_000_000), "Large Cap")
        self.assertEqual(self.pp._classify_market_cap(5_000_000_000), "Mid Cap")
        self.assertEqual(self.pp._classify_market_cap(800_000_000), "Small Cap")
        self.assertEqual(self.pp._classify_market_cap(100_000_000), "Micro Cap")
        self.assertEqual(self.pp._classify_market_cap(None), "Unknown")

    def test_add_position_with_price_shares_persists_categories(self):
        profile = {
            "sector": "Technology",
            "market_cap": 3_000_000_000_000,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            result = self.pp.add_position(
                "u1",
                "aapl",
                "LONG",
                price=200,
                shares=2.5,
                source="manual_price_shares",
            )

        self.assertNotIn("error", result)
        self.assertEqual(result["allocated"], 500)
        rows = self.pp.get_positions("u1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sector"], "Technology")
        self.assertEqual(rows[0]["cap_bucket"], "Mega Cap")
        self.assertEqual(rows[0]["asset_type"], "Equity")

    def test_import_insert_persists_categories(self):
        profile = {
            "sector": "Consumer Cyclical",
            "market_cap": 20_000_000_000,
            "cap_bucket": "Large Cap",
            "asset_type": "Equity",
        }
        with (
            patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile),
            patch("backend.paper_portfolio._resolve_import_entry_price", return_value=50.0),
        ):
            result = self.pp.apply_holdings_import(
                "u1",
                [{"ticker": "SHOP", "shares": 3, "avg_cost": 50}],
                source="screenshot_import",
            )

        self.assertEqual(result["applied"], ["SHOP"])
        rows = self.pp.get_positions("u1")
        self.assertEqual(rows[0]["sector"], "Consumer Cyclical")
        self.assertEqual(rows[0]["cap_bucket"], "Large Cap")


if __name__ == "__main__":
    unittest.main()
