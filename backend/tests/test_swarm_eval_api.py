"""API tests for in-app SwarmScore evaluator."""

import os
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient

from backend.main import app


class TestSwarmEvalApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_run_and_fetch_report(self) -> None:
        r = self.client.post("/admin/swarm-score/run", json={"mode": "fixture"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("run_id"))
        self.assertIn("winner", body)

        summary = self.client.get("/admin/swarm-score/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json().get("run_id"), body.get("run_id"))

        report = self.client.get("/admin/swarm-score/report?format=json")
        self.assertEqual(report.status_code, 200)
        self.assertIn("Weekly Swarm Effectiveness Report", report.json().get("markdown", ""))


if __name__ == "__main__":
    unittest.main()
