"""Router contract tests for the DB-backed Fund Leaderboard endpoints."""
import os
import tempfile
import unittest

# Bind to an isolated temp DB before importing the app/store.
_TMPDIR = tempfile.TemporaryDirectory()
os.environ["FUND_LEADERBOARD_DB_PATH"] = os.path.join(_TMPDIR.name, "router_fl.db")

from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402
from backend import fund_leaderboard_store as store  # noqa: E402

if hasattr(store._local, "conn"):
    del store._local.conn
store.init_schema()

client = TestClient(app)


class FundLeaderboardRouterTest(unittest.TestCase):
    def test_leaderboard_empty_mode_returns_message(self):
        response = client.get("/api/funds/leaderboard?mode=reported")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["rows"], [])
        self.assertIn("disclaimer", data)
        self.assertIn("message", data)

    def test_leaderboard_returns_persisted_rows(self):
        rows = [
            {"rank": 1, "fundId": "fund-a", "fundName": "Alpha Capital", "cagr10Y": 0.28,
             "alphaVsSP500": 0.06, "sharpe10Y": 1.4, "maxDrawdown10Y": -0.15,
             "latest13FValueUsd": 5.0e9, "dataConfidenceScore": 88,
             "dataConfidenceLabel": "Good", "leaderboardScore": 0.9},
        ]
        store.write_leaderboard_snapshot("2025-06-25", "2024-12-31", store.DEFAULT_MODE, rows)

        response = client.get("/api/funds/leaderboard?mode=13f_investable")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["fundName"], "Alpha Capital")
        self.assertEqual(data["methodologyVersion"], store.METHODOLOGY_VERSION)

    def test_portfolio_endpoint_404_for_unknown_fund(self):
        response = client.get("/api/funds/does-not-exist/portfolio/latest")
        self.assertEqual(response.status_code, 404)

    def test_returns_endpoint_404_for_unknown_fund(self):
        response = client.get("/api/funds/does-not-exist/returns")
        self.assertEqual(response.status_code, 404)

    def test_ingest_status_endpoint(self):
        response = client.get("/api/funds/ingest/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("run", data)
        self.assertIn("fundsTracked", data)


if __name__ == "__main__":
    unittest.main()
