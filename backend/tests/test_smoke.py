"""
Local / CI smoke tests — no network-heavy routes (no full debate/trace).
Run from repo root:  ./scripts/run_backend_tests.sh  (needs Python 3.10+)

Optional slow check (full swarm+debate+terminal assembly, ~15–90s):
  RUN_DECISION_TERMINAL_SMOKE=1 ./scripts/run_backend_tests.sh
"""
import json
import os
import unittest

from fastapi.testclient import TestClient
from backend.main import app


class TestSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_openapi(self):
        r = self.client.get("/openapi.json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("openapi", r.json())

    def test_debate_invalid_ticker_422(self):
        r = self.client.get("/debate", params={"ticker": "BAD!!"})
        self.assertEqual(r.status_code, 422)
        detail = r.json().get("detail", {})
        assert isinstance(detail, dict)
        self.assertEqual(detail.get("error"), "invalid_ticker")

    def test_strategy_presets(self):
        r = self.client.get("/strategies/presets")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("presets", data)
        self.assertIsInstance(data["presets"], list)
        self.assertGreaterEqual(len(data["presets"]), 1)

    def test_request_id_middleware(self):
        r = self.client.get(
            "/strategies/presets",
            headers={"X-Request-ID": "smoke-test-uuid"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("X-Request-ID"), "smoke-test-uuid")

    @unittest.skipUnless(
        os.environ.get("RUN_DECISION_TERMINAL_SMOKE", "").strip().lower() in ("1", "true", "yes"),
        "set RUN_DECISION_TERMINAL_SMOKE=1 to run (slow: live tools + debate)",
    )
    def test_decision_terminal_returns_200_and_json(self):
        """Guards against HTTP 500 / non-JSON floats (e.g. NaN) on the decision terminal path."""
        r = self.client.get("/decision-terminal", params={"ticker": "AAPL"})
        self.assertEqual(r.status_code, 200, r.text[:500])
        data = r.json()
        self.assertEqual(data.get("ticker"), "AAPL")
        json.dumps(data)
        self.assertIn("valuation", data)
        self.assertIn("verdict", data)


if __name__ == "__main__":
    unittest.main()
