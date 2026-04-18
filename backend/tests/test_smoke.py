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


class TestGeminiPrimaryConfigSmoke(unittest.TestCase):
    """
    Offline (no-network) smoke: flipping ``GEMINI_PRIMARY=1`` must not break app
    bootstrap or LLM routing wiring. Complements the live tests in
    :mod:`test_gemini_live_smoke` (which require an API key) by catching
    import-time regressions that live tests might not exercise if they're
    skipped on a dev box.
    """

    def setUp(self):
        self._saved_primary = os.environ.get("GEMINI_PRIMARY")
        self._saved_key = os.environ.get("GEMINI_API_KEY")
        # A dummy key is enough to flip ``gemini_primary_enabled()`` to True —
        # no network call is attempted by the tests in this class.
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "smoke-dummy-key"

    def tearDown(self):
        for name, saved in (
            ("GEMINI_PRIMARY", self._saved_primary),
            ("GEMINI_API_KEY", self._saved_key),
        ):
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved

    def test_openapi_still_boots_with_gemini_primary(self):
        """App must still expose /openapi.json with GEMINI_PRIMARY=1."""
        client = TestClient(app)
        r = client.get("/openapi.json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("openapi", r.json())

    def test_gemini_primary_flag_reads_true(self):
        from backend.gemini_llm import gemini_primary_enabled

        self.assertTrue(gemini_primary_enabled())

    def test_role_to_gemini_model_routing(self):
        """Heavy vs light role mapping survives the flag flip."""
        from backend.gemini_llm import GEMINI_MODEL, GEMINI_MODEL_LIGHT
        from backend.llm_client import _gemini_model_for_role

        self.assertEqual(_gemini_model_for_role("bull"), GEMINI_MODEL)
        self.assertEqual(_gemini_model_for_role("moderator"), GEMINI_MODEL)
        self.assertEqual(_gemini_model_for_role("swarm_analyst"), GEMINI_MODEL_LIGHT)
        self.assertEqual(
            _gemini_model_for_role("video_veo_text_fallback"), GEMINI_MODEL_LIGHT
        )


if __name__ == "__main__":
    unittest.main()
