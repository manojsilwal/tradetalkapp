"""
Local / CI smoke tests — no network-heavy routes (no full debate/trace).
Run from repo root:  python -m unittest discover -s backend/tests -p 'test_*.py' -v
"""
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


if __name__ == "__main__":
    unittest.main()
