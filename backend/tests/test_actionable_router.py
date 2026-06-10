"""HTTP integration tests for /actionable-companies (202 contract, status, results)."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient

from backend import actionable_companies as svc
from backend.main import app


def _row(ticker: str, score: float, verdict: str, actionable: bool = True) -> dict:
    return {
        "ticker": ticker,
        "company_name": f"{ticker} Inc",
        "sector": "Tech",
        "score": score,
        "verdict": verdict,
        "actionable": actionable,
        "coverage": 0.9,
        "pillars": {},
        "fundamentals": {},
        "momentum": {},
    }


class TestActionableRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ACTIONABLE_DB_PATH"] = os.path.join(self._tmp.name, "actionable.db")
        svc._set_job(
            job_id=None, status="idle", progress=0, message="", processed=0,
            total=0, snapshot_id=None, cache_hit=False, error=None,
        )

    def tearDown(self):
        os.environ.pop("ACTIONABLE_DB_PATH", None)
        self._tmp.cleanup()

    def test_run_returns_202_and_hands_off_to_worker(self):
        async def _fake_scan(job_id, *, force=False):
            svc._set_job(job_id=job_id, status="done", progress=100, snapshot_id="snap_x")
            return {"snapshot_id": "snap_x"}

        with patch.object(svc, "run_actionable_scan", side_effect=_fake_scan):
            r = self.client.post("/actionable-companies/run")
        self.assertEqual(r.status_code, 202)
        body = r.json()
        self.assertTrue(body["accepted"])
        self.assertFalse(body["cache_hit"])
        self.assertIn("job", body)
        self.assertIsNotNone(body["job"]["job_id"])

    def test_run_rejected_while_running(self):
        svc._set_job(status="running", job_id="busy123", progress=40)
        r = self.client.post("/actionable-companies/run")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["accepted"])
        self.assertEqual(body["reason"], "already_running")
        self.assertEqual(body["job"]["job_id"], "busy123")

    def test_run_serves_fresh_snapshot_from_cache(self):
        svc.persist_snapshot("snap_fresh", [_row("AAPL", 75.0, "Strong Buy")], universe_size=1, skipped=0)
        r = self.client.post("/actionable-companies/run")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["accepted"])
        self.assertTrue(body["cache_hit"])
        self.assertEqual(body["snapshot"]["snapshot_id"], "snap_fresh")

    def test_status_endpoint(self):
        svc._set_job(status="running", job_id="j1", progress=55, message="Scoring…")
        r = self.client.get("/actionable-companies/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "running")
        self.assertEqual(body["progress"], 55)

    def test_results_empty(self):
        r = self.client.get("/actionable-companies/results")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["snapshot"])
        self.assertEqual(body["rows"], [])

    def test_results_sorted_and_filtered(self):
        svc.persist_snapshot(
            "snap_r",
            [
                _row("AAA", 80.0, "Strong Buy"),
                _row("BBB", 50.0, "Hold", actionable=False),
                _row("CCC", 65.0, "Buy"),
            ],
            universe_size=3,
            skipped=0,
        )
        r = self.client.get("/actionable-companies/results?limit=10")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["snapshot"]["snapshot_id"], "snap_r")
        self.assertTrue(body["is_fresh"])
        self.assertEqual([x["ticker"] for x in body["rows"]], ["AAA", "CCC"])

        r_all = self.client.get("/actionable-companies/results?actionable_only=false")
        self.assertEqual(len(r_all.json()["rows"]), 3)


if __name__ == "__main__":
    unittest.main()
