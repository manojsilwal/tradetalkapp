"""Offline tests for the Fund Leaderboard store (SQLite backend)."""
import os
import tempfile
import unittest


class FundLeaderboardStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        os.environ["FUND_LEADERBOARD_DB_PATH"] = os.path.join(cls._tmpdir.name, "fl.db")

        from backend import fund_leaderboard_store as store
        # Reset any cached threadlocal connection so we bind to our temp DB path.
        if hasattr(store._local, "conn"):
            del store._local.conn
        cls.store = store
        store.init_schema()

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def test_upsert_fund_is_idempotent_by_cik(self):
        store = self.store
        fid1 = store.upsert_fund("0001067983", "Berkshire Hathaway", manager_type="institutional")
        fid2 = store.upsert_fund("0001067983", "Berkshire Hathaway Inc", latest_aum_usd=3.5e11)
        self.assertEqual(fid1, fid2)
        fund = store.get_fund_by_cik("0001067983")
        self.assertEqual(fund["display_name"], "Berkshire Hathaway Inc")

    def test_filing_holdings_and_portfolio(self):
        store = self.store
        fid = store.upsert_fund("0000111", "Test Capital")
        filing_id = store.upsert_filing(
            fund_id=fid, cik="0000111", accession_number="acc-1",
            form_type="13F-HR", report_period="2024-12-31",
            filing_date="2025-02-14", filing_url="http://x", total_market_value_usd=1000.0,
        )
        store.replace_holdings(filing_id, fid, "2024-12-31", [
            {"issuer_name": "Apple", "cusip": "037833100", "ticker": "AAPL",
             "sector": "Information Technology", "shares": 10, "market_value_usd": 600.0, "holding_weight": 0.6,
             "mapping_status": "mapped"},
            {"issuer_name": "Microsoft", "cusip": "594918104", "ticker": "MSFT",
             "sector": "Information Technology", "shares": 5, "market_value_usd": 400.0, "holding_weight": 0.4,
             "mapping_status": "mapped"},
        ])
        portfolio = store.get_fund_portfolio_latest(fid)
        self.assertEqual(portfolio["fundName"], "Test Capital")
        self.assertEqual(len(portfolio["holdings"]), 2)
        self.assertAlmostEqual(portfolio["mappedMarketValuePct"], 1.0, places=3)
        self.assertEqual(portfolio["sectorAllocation"][0]["sector"], "Information Technology")

    def test_cusip_cache_roundtrip(self):
        store = self.store
        self.assertIsNone(store.cache_get_ticker("999999999"))
        store.cache_put_ticker("999999999", "ABC", "ABC Corp", "Industrials", "mapped")
        cached = store.cache_get_ticker("999999999")
        self.assertEqual(cached["ticker"], "ABC")

    def test_return_metrics_roundtrip(self):
        store = self.store
        fid = store.upsert_fund("0000222", "Returns Fund")
        store.upsert_return_metrics(
            fund_id=fid, mode=store.DEFAULT_MODE, period="5Y", as_of_date="2025-06-25",
            metrics={"cagr": 0.21, "alphaVsBenchmark": 0.04, "sharpe": 1.2,
                     "sortino": 1.5, "maxDrawdown": -0.18, "positiveQuarterRate": 0.7,
                     "roicProxy": 2.6},
            data_confidence_score=80,
            series=[{"periodEnd": "2024-12-31", "cumulativeValue": 1.2, "benchmarkCumulativeValue": 1.1, "drawdown": 0.0}],
        )
        ret = store.get_fund_returns(fid, mode=store.DEFAULT_MODE, period="5Y")
        self.assertEqual(ret["metrics"]["cagr"], 0.21)
        self.assertEqual(len(ret["series"]), 1)

    def test_leaderboard_snapshot_ranking(self):
        store = self.store
        rows = [
            {"rank": 1, "fundId": "a", "fundName": "Alpha", "cagr10Y": 0.30,
             "dataConfidenceScore": 90, "dataConfidenceLabel": "High", "leaderboardScore": 0.9},
            {"rank": 2, "fundId": "b", "fundName": "Beta", "cagr10Y": 0.18,
             "dataConfidenceScore": 40, "dataConfidenceLabel": "Low", "leaderboardScore": 0.5},
        ]
        store.write_leaderboard_snapshot("2025-06-25", "2024-12-31", store.DEFAULT_MODE, rows)

        result = store.get_leaderboard(mode=store.DEFAULT_MODE, limit=50)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["fundName"], "Alpha")
        self.assertIn("disclaimer", result)

        # min_confidence filter drops the low-confidence row
        filtered = store.get_leaderboard(mode=store.DEFAULT_MODE, limit=50, min_confidence=60)
        self.assertEqual(len(filtered["rows"]), 1)
        self.assertEqual(filtered["rows"][0]["fundName"], "Alpha")

        # Re-writing replaces the snapshot rather than appending
        store.write_leaderboard_snapshot("2025-06-26", "2024-12-31", store.DEFAULT_MODE, rows[:1])
        again = store.get_leaderboard(mode=store.DEFAULT_MODE, limit=50)
        self.assertEqual(len(again["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
