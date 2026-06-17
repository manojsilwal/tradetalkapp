"""HTTP integration tests for /daily-brief/screener with mocked snapshot loader."""
from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient
from backend.main import app


class TestScreenerRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @patch("backend.routers.daily_brief.overlay_realtime_quotes", side_effect=lambda x, *args, **kwargs: x)
    @patch("backend.daily_brief.load_snapshot")
    def test_screener_returns_actionable_signals(self, mock_load, mock_overlay):
        # Setup mock return value of load_snapshot
        mock_load.return_value = {
            "trade_date": "2026-06-02",
            "source": "sp500_screener",
            "verdict_tier": "deep",
            "updated_at": "2026-06-02T12:00:00Z",
            "rows": [
                {"symbol": "AAPL", "verdict": "Strong Buy", "preset": "growth", "revenue_growth_pct": 18.0},
                {"symbol": "MSFT", "verdict": "Hold", "preset": "value", "revenue_growth_pct": 8.0},
                {"symbol": "T", "verdict": "Buy", "preset": "income", "dividend_yield_pct": 6.5},
                {"symbol": "NFLX", "verdict": "Sell", "preset": "growth", "eps_growth_pct": -10.0},
            ]
        }

        r = self.client.get("/daily-brief/screener")
        self.assertEqual(r.status_code, 200)
        body = r.json()

        self.assertTrue(mock_load.called)
        self.assertEqual(body["trade_date"], "2026-06-02")
        self.assertEqual(body["source"], "sp500_screener")
        
        # Verify that ONLY actionable rows (Strong Buy, Buy, Sell) are returned.
        # MSFT (Hold) should be filtered out!
        symbols = [row["symbol"] for row in body["rows"]]
        self.assertEqual(set(symbols), {"AAPL", "T", "NFLX"})
        self.assertNotIn("MSFT", symbols)

        # Check preset data properties are retained
        for row in body["rows"]:
            self.assertIn("preset", row)

    @patch("backend.routers.daily_brief.overlay_realtime_quotes", side_effect=lambda x, *args, **kwargs: x)
    @patch("backend.daily_brief.load_snapshot")
    def test_screener_handles_empty_snapshot(self, mock_load, mock_overlay):
        mock_load.return_value = None

        r = self.client.get("/daily-brief/screener?trade_date=2026-06-02")
        self.assertEqual(r.status_code, 200)
        body = r.json()

        self.assertEqual(body["source"], "none")
        self.assertEqual(body["rows"], [])
        self.assertIn("No pre-scored daily snapshot", body["message"])


if __name__ == "__main__":
    unittest.main()
