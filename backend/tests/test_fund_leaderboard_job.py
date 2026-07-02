"""Offline tests for the Fund Leaderboard job: reconstruction + scoring + snapshot build.

No live SEC/yfinance/OpenFIGI calls — uses synthetic price frames and fixtures.
"""
import os
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd


class LeaderboardScoringTest(unittest.TestCase):
    def test_rank_orders_by_composite_score(self):
        from backend.coral_skills.leaderboard_scoring import rank_leaderboard

        funds = [
            {"fundId": "low", "fundName": "Low", "metrics": {
                "cagr": 0.05, "alphaVsBenchmark": -0.02, "sharpe": 0.3,
                "sortino": 0.4, "maxDrawdown": -0.40, "positiveQuarterRate": 0.4},
             "confidence": {"score": 50, "components": {"track_record_score": 50}}},
            {"fundId": "high", "fundName": "High", "metrics": {
                "cagr": 0.30, "alphaVsBenchmark": 0.10, "sharpe": 1.8,
                "sortino": 2.1, "maxDrawdown": -0.10, "positiveQuarterRate": 0.85},
             "confidence": {"score": 92, "components": {"track_record_score": 100}}},
            {"fundId": "mid", "fundName": "Mid", "metrics": {
                "cagr": 0.15, "alphaVsBenchmark": 0.03, "sharpe": 1.0,
                "sortino": 1.2, "maxDrawdown": -0.20, "positiveQuarterRate": 0.6},
             "confidence": {"score": 75, "components": {"track_record_score": 80}}},
        ]
        ranked = rank_leaderboard(funds)
        self.assertEqual(ranked[0]["fundId"], "high")
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[-1]["fundId"], "low")
        self.assertIn("leaderboard_score", ranked[0])


class CloneReturnsTest(unittest.TestCase):
    def _price_frame(self, tickers, start, end, daily_drift):
        idx = pd.bdate_range(start=start, end=end)
        cols = {}
        for t, drift in zip(tickers, daily_drift):
            prices = 100.0 * np.cumprod(1 + np.full(len(idx), drift))
            cols[(t, "Close")] = prices
        df = pd.DataFrame(cols, index=idx)
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        return df

    def test_calculate_clone_returns_positive_alpha(self):
        from backend.coral_skills.return_reconstruction import calculate_clone_returns

        start = (date.today() - timedelta(days=500)).isoformat()
        end = date.today().isoformat()
        prices = self._price_frame(["AAA", "BBB"], start, end, [0.0010, 0.0008])
        bench = self._price_frame(["SPY"], start, end, [0.0004])

        snapshots = [{
            "filing_date": (date.today() - timedelta(days=480)).isoformat(),
            "report_period": (date.today() - timedelta(days=520)).isoformat(),
            "holdings": [{"ticker": "AAA", "weight": 0.5}, {"ticker": "BBB", "weight": 0.5}],
        }]

        result = calculate_clone_returns(snapshots, prices, bench)
        self.assertNotIn("error", result)
        self.assertGreater(result["metrics"]["cagr"], 0)
        # Portfolio drifts faster than the benchmark -> positive alpha
        self.assertGreater(result["metrics"]["alphaVsBenchmark"], 0)
        self.assertTrue(len(result["series"]) >= 1)
        self.assertIn("cumulativeValue", result["series"][0])


class BuildSnapshotsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        os.environ["FUND_LEADERBOARD_DB_PATH"] = os.path.join(cls._tmpdir.name, "job.db")

        from backend import fund_leaderboard_store as store
        if hasattr(store._local, "conn"):
            del store._local.conn
        store.init_schema()
        cls.store = store

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def test_build_snapshots_and_persist(self):
        from backend import fund_leaderboard_job as job
        store = self.store

        fund_id = store.upsert_fund("0000333", "Builder Capital")
        parsed = [
            {
                "cik": "0000333", "accession_number": "a-1", "form_type": "13F-HR",
                "report_period": "2024-09-30", "filing_date": "2024-11-14",
                "filing_url": "http://x/1",
                "holdings": [
                    {"issuer_name": "Apple", "cusip": "037833100", "ticker": "AAPL",
                     "sector": "Tech", "market_value_usd": 700.0, "mapping_status": "mapped"},
                    {"issuer_name": "Unknown Co", "cusip": "000000000", "ticker": None,
                     "sector": "Unknown", "market_value_usd": 300.0, "mapping_status": "unmapped"},
                ],
            },
            {
                "cik": "0000333", "accession_number": "a-2", "form_type": "13F-HR",
                "report_period": "2024-12-31", "filing_date": "2025-02-14",
                "filing_url": "http://x/2",
                "holdings": [
                    {"issuer_name": "Apple", "cusip": "037833100", "ticker": "AAPL",
                     "sector": "Tech", "market_value_usd": 800.0, "mapping_status": "mapped"},
                ],
            },
        ]

        built = job._build_snapshots_and_persist(fund_id, parsed)
        self.assertEqual(built["tickers"], ["AAPL"])
        # Two snapshots, weights computed; last snapshot is fully mapped
        self.assertEqual(len(built["snapshots"]), 2)
        self.assertEqual(built["latest_report_period"], "2024-12-31")

        # Holdings persisted; latest filing portfolio reflects the newest period
        portfolio = store.get_fund_portfolio_latest(fund_id)
        self.assertEqual(portfolio["reportPeriod"], "2024-12-31")
        self.assertEqual(len(portfolio["holdings"]), 1)

    def test_presentable_row_shape(self):
        from backend import fund_leaderboard_job as job
        scored = {
            "fundId": "x", "fundName": "X Capital", "managerType": "Institutional",
            "strategyTags": [], "rank": 1, "leaderboard_score": 0.88,
            "metrics": {"cagr": 0.22, "alphaVsBenchmark": 0.05, "sharpe": 1.3,
                        "maxDrawdown": -0.2, "roicProxy": 2.7},
            "confidence": {"score": 84, "label": "Good"},
            "latest13FValueUsd": 1.2e9, "latestReportPeriod": "2024-12-31",
            "topSector": "Tech", "topSectorWeight": 0.4, "top10HoldingsWeight": 0.7,
            "lastFilingDate": "2025-02-14",
        }
        row = job._to_presentable_row(scored)
        self.assertEqual(row["cagr10Y"], 0.22)
        self.assertEqual(row["dataConfidenceLabel"], "Good")
        self.assertEqual(row["leaderboardScore"], 0.88)

    def test_presentable_row_preserves_philosophy_and_tags(self):
        from backend import fund_leaderboard_job as job
        scored = {
            "fundId": "p", "fundName": "Pershing Square", "managerType": "hedge_fund",
            "strategyTags": ["activist", "concentrated"],
            "philosophy": "Concentrated activist bets on quality businesses.",
            "emerging": False, "rank": 2, "leaderboard_score": 0.7,
            "metrics": {"cagr": 0.18}, "confidence": {"score": 80, "label": "Good"},
            "latest13FValueUsd": 1e10, "latestReportPeriod": "2025-12-31",
        }
        row = job._to_presentable_row(scored)
        self.assertEqual(row["managerType"], "hedge_fund")
        self.assertEqual(row["strategyTags"], ["activist", "concentrated"])
        self.assertEqual(row["philosophy"], "Concentrated activist bets on quality businesses.")
        self.assertFalse(row["emerging"])

    def test_presentable_row_emerging_has_null_returns(self):
        from backend import fund_leaderboard_job as job
        scored = {
            "fundId": "s", "fundName": "Situational Awareness LP", "managerType": "hedge_fund",
            "strategyTags": ["ai", "thematic"], "philosophy": "Concentrated AI thesis.",
            "emerging": True, "metrics": {}, "confidence": {"score": 0, "label": "Emerging"},
            "latest13FValueUsd": 1.3e10, "latestReportPeriod": "2026-03-31",
        }
        row = job._to_presentable_row(scored)
        self.assertTrue(row["emerging"])
        self.assertIsNone(row["cagr10Y"])
        self.assertIsNone(row["alphaVsSP500"])
        self.assertEqual(row["dataConfidenceLabel"], "Emerging")
        self.assertEqual(row["philosophy"], "Concentrated AI thesis.")


class MetricsRefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        os.environ["FUND_LEADERBOARD_DB_PATH"] = os.path.join(cls._tmpdir.name, "metrics.db")
        os.environ["FUND_LB_EMERGING_MIN_QUARTERS"] = "2"
        os.environ["FUND_LB_MIN_QUARTERS"] = "8"

        from backend import fund_leaderboard_store as store
        if hasattr(store._local, "conn"):
            del store._local.conn
        store.init_schema()
        cls.store = store

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def _seed_fund_with_filings(self):
        from backend import fund_leaderboard_job as job

        fund_id = self.store.upsert_fund("0000888", "Metrics Refresh Capital")
        parsed = [
            {
                "cik": "0000888", "accession_number": "m-1", "form_type": "13F-HR",
                "report_period": "2024-09-30", "filing_date": "2024-11-14",
                "filing_url": "http://x/m1",
                "holdings": [
                    {"issuer_name": "Apple", "cusip": "037833100", "ticker": "AAPL",
                     "sector": "Tech", "market_value_usd": 1000.0, "mapping_status": "mapped"},
                ],
            },
            {
                "cik": "0000888", "accession_number": "m-2", "form_type": "13F-HR",
                "report_period": "2024-12-31", "filing_date": "2025-02-14",
                "filing_url": "http://x/m2",
                "holdings": [
                    {"issuer_name": "Apple", "cusip": "037833100", "ticker": "AAPL",
                     "sector": "Tech", "market_value_usd": 1200.0, "mapping_status": "mapped"},
                ],
            },
        ]
        job._build_snapshots_and_persist(fund_id, parsed)
        return fund_id

    def test_snapshots_from_db(self):
        from backend import fund_leaderboard_job as job

        fund_id = self._seed_fund_with_filings()
        built = job._snapshots_from_db(fund_id, max_quarters=20)
        self.assertEqual(built["tickers"], ["AAPL"])
        self.assertEqual(len(built["snapshots"]), 2)
        self.assertEqual(built["quarter_count"], 2)

    def test_list_funds_with_min_filings(self):
        self._seed_fund_with_filings()
        funds = self.store.list_funds_with_min_filings(2)
        self.assertEqual(len(funds), 1)
        self.assertEqual(funds[0]["cik"], "0000888")

    def test_run_metrics_refresh_job_emerging(self):
        import asyncio
        from datetime import date
        from backend import fund_leaderboard_job as job

        fund_id = self._seed_fund_with_filings()
        # Prod path: refresh funds already on the leaderboard snapshot.
        self.store.write_leaderboard_snapshot(
            date.today().isoformat(),
            "2024-12-31",
            self.store.DEFAULT_MODE,
            [{
                "rank": 1,
                "fundId": fund_id,
                "fundName": "Metrics Refresh Capital",
                "managerType": "Institutional",
                "strategyTags": [],
                "emerging": True,
                "cagr10Y": None,
                "dataConfidenceScore": 0,
                "dataConfidenceLabel": "Emerging",
                "latestReportPeriod": "2024-12-31",
            }],
        )
        summary = asyncio.run(job.run_metrics_refresh_job(top_n=10))
        self.assertEqual(summary.get("job_type"), "metrics_refresh")
        self.assertGreaterEqual(summary.get("leaderboard_rows", 0), 1)
        lb = self.store.get_leaderboard(limit=10)
        self.assertGreaterEqual(len(lb.get("rows") or []), 1)


if __name__ == "__main__":
    unittest.main()
