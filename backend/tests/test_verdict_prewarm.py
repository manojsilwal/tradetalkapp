"""Cron auth + routing smoke for verdict prewarm endpoint."""
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.main import app


class TestVerdictPrewarmRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "cron-test"})
    @patch("backend.verdict_prewarm.run_verdict_prewarm", new_callable=AsyncMock)
    def test_prewarm_requires_secret(self, mock_run):
        mock_run.return_value = {"tickers_requested": 1, "results": []}
        r = self.client.post("/decision-terminal/prewarm")
        self.assertEqual(r.status_code, 401)

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "cron-test"})
    @patch("backend.verdict_prewarm.run_verdict_prewarm", new_callable=AsyncMock)
    def test_prewarm_ok_with_bearer(self, mock_run):
        mock_run.return_value = {"tickers_requested": 2, "cache_hits": 1, "cold_runs": 1, "results": []}
        r = self.client.post(
            "/decision-terminal/prewarm?tickers=AAPL,MSFT",
            headers={"Authorization": "Bearer cron-test"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["tickers_requested"], 2)
        mock_run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
