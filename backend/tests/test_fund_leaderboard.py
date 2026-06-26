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

    def test_top_endpoint(self):
        response = client.get("/api/funds/top?mode=13f_investable&limit=10")
        self.assertEqual(response.status_code, 200)
        self.assertIn("rows", response.json())

    def test_cik_endpoints_roundtrip(self):
        # Seed a fund + filing + holdings + quarterly summary, then read via CIK.
        cik = "9990001"
        fid = store.upsert_fund(cik, "CIK Endpoint Co", latest_13f_value_usd=1.0e9)
        filing_id = store.upsert_filing(
            fund_id=fid, cik=cik, accession_number="cik-acc-1", form_type="13F-HR",
            report_period="2024-12-31", filing_date="2025-02-10", filing_url="http://x",
            total_market_value_usd=1000.0, parse_status="parsed",
        )
        store.replace_holdings(filing_id, fid, "2024-12-31", [
            {"issuer_name": "Apple", "cusip": "037833100", "ticker": "AAPL",
             "sector": "Tech", "shares": 10, "market_value_usd": 1000.0, "holding_weight": 1.0},
        ])
        store.upsert_quarterly_summary(fid, cik, {
            "period_of_report": "2024-12-31", "prev_period": None,
            "total_13f_value_usd": 1000.0, "holdings_count": 1,
            "top10_concentration": 1.0, "top20_concentration": 1.0,
            "turnover_estimate_pct": None, "new_count": 1, "soldout_count": 0,
            "increased_count": 0, "decreased_count": 0, "unchanged_count": 0,
            "changes": {"new": [{"ticker": "AAPL"}], "soldOut": [], "increased": [], "decreased": []},
            "sector_flow": [{"sector": "Tech", "netFlowUsd": 1000.0}],
        })

        r = client.get(f"/api/funds/{cik}/filings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["filings"]), 1)

        r = client.get(f"/api/funds/{cik}/holdings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["holdings"][0]["ticker"], "AAPL")

        r = client.get(f"/api/funds/{cik}/changes")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["counts"]["new"], 1)

        r = client.get(f"/api/funds/{cik}/timeline")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["timeline"]), 1)

    def test_cik_endpoints_404(self):
        self.assertEqual(client.get("/api/funds/0000000/filings").status_code, 404)
        self.assertEqual(client.get("/api/funds/0000000/holdings").status_code, 404)
        self.assertEqual(client.get("/api/funds/0000000/changes").status_code, 404)
        self.assertEqual(client.get("/api/funds/0000000/timeline").status_code, 404)


if __name__ == "__main__":
    unittest.main()
