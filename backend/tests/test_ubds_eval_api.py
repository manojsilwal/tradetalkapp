"""API tests for in-app UBDS benchmark."""

import os
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient

from backend.main import app


class TestUbdsEvalApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_run_and_fetch_report(self) -> None:
        r = self.client.post("/admin/ubds/run", json={"mode": "fixture"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("run_id"))
        self.assertIn(body.get("status"), ("pass", "hold", "fail"))

        summary = self.client.get("/admin/ubds/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json().get("run_id"), body.get("run_id"))

        report = self.client.get("/admin/ubds/report?format=json")
        self.assertEqual(report.status_code, 200)
        self.assertIn("UI Behavior & Design Benchmark Report", report.json().get("markdown", ""))


if __name__ == "__main__":
    unittest.main()
