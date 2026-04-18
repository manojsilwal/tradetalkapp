"""
Integration tests proving the contract validator fires end-to-end through
:class:`~backend.llm_client.LLMClient._provider_generate`.

We don't hit a real provider. Instead we patch the synchronous OpenRouter call
path so ``_provider_generate`` believes it got a completion back, then watch
what ``_parse_json_response`` returns and what the validator does with it.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend import contract_validator as cv  # noqa: E402
from backend import resource_registry as rr  # noqa: E402
from backend.resource_seeder import seed_resources_if_empty  # noqa: E402
from backend import llm_client as lc  # noqa: E402
from backend.resources.prompts import __file__ as _prompts_marker  # noqa: F401


BULL_FALLBACK = {
    "headline": "Bullish signals detected in available market data.",
    "key_points": [
        "Short interest and squeeze potential identified.",
        "Positive revenue growth trend supports upside thesis.",
        "Sentiment indicators lean constructive.",
    ],
    "confidence": 0.55,
}


class _IntegrationBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["RESOURCES_DB_PATH"] = os.path.join(self._tmp.name, "r.db")
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        os.environ["CONTRACT_VALIDATOR_ENABLE"] = "1"
        # Force the OpenRouter inference path so the fake pool is exercised.
        # Setting unconditionally (not setdefault) so earlier tests can't
        # leave GEMINI_PRIMARY=1 sticking around and skip our pool.
        self._prev_gemini_primary = os.environ.get("GEMINI_PRIMARY")
        self._prev_gemini_fallback = os.environ.get("GEMINI_LLM_FALLBACK")
        os.environ["GEMINI_PRIMARY"] = "0"
        os.environ["GEMINI_LLM_FALLBACK"] = "0"
        rr._reset_singleton_for_tests()
        self.reg = rr.get_resource_registry()
        seed_resources_if_empty(self.reg)
        cv._reset_singleton_for_tests()

        self.client = lc.LLMClient()

        self.captured: list = []

        def capture_sink(v: cv.ContractViolation, ctx: dict) -> None:
            self.captured.append((v, dict(ctx)))

        cv.get_contract_validator().set_sink(capture_sink)

    def tearDown(self) -> None:
        rr._reset_singleton_for_tests()
        cv._reset_singleton_for_tests()
        os.environ.pop("RESOURCES_DB_PATH", None)
        if self._prev_gemini_primary is None:
            os.environ.pop("GEMINI_PRIMARY", None)
        else:
            os.environ["GEMINI_PRIMARY"] = self._prev_gemini_primary
        if self._prev_gemini_fallback is None:
            os.environ.pop("GEMINI_LLM_FALLBACK", None)
        else:
            os.environ["GEMINI_LLM_FALLBACK"] = self._prev_gemini_fallback


class TestEnforceContractHelper(_IntegrationBase):
    def test_resolve_contract_returns_seeded_schema_and_fallback(self) -> None:
        schema, fallback = self.client._resolve_contract("bull")
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema.get("required"), ["headline", "key_points", "confidence"])
        self.assertIsInstance(fallback, dict)
        self.assertIn("headline", fallback)

    def test_valid_payload_passes_through_unchanged(self) -> None:
        good = {"headline": "x", "key_points": ["a"], "confidence": 0.5}
        out = self.client._enforce_contract(
            good, role="bull", prompt_version="1.0.0", model="test-model"
        )
        self.assertEqual(out, good)
        self.assertEqual(self.captured, [])

    def test_missing_required_coerces_to_fallback(self) -> None:
        bad = {"headline": "only-headline"}  # missing key_points + confidence
        out = self.client._enforce_contract(
            bad, role="bull", prompt_version="1.0.0", model="test-model"
        )
        self.assertEqual(out, BULL_FALLBACK)
        self.assertTrue(self.captured)
        codes = {v.code for v, _ctx in self.captured}
        self.assertIn("missing_required", codes)
        # Context is stamped with role / model / version for drift analytics
        _, ctx = self.captured[0]
        self.assertEqual(ctx["role"], "bull")
        self.assertEqual(ctx["version"], "1.0.0")
        self.assertEqual(ctx["model"], "test-model")

    def test_unknown_role_is_passthrough(self) -> None:
        # No schema registered -> validator returns the input unchanged.
        data = {"arbitrary": 1}
        out = self.client._enforce_contract(
            data, role="does_not_exist", prompt_version="unversioned", model="m"
        )
        self.assertEqual(out, data)
        self.assertEqual(self.captured, [])

    def test_validator_disabled_is_passthrough(self) -> None:
        os.environ["CONTRACT_VALIDATOR_ENABLE"] = "0"
        try:
            bad = {"headline": "x"}  # fatal under schema
            out = self.client._enforce_contract(
                bad, role="bull", prompt_version="1.0.0", model="m"
            )
            self.assertEqual(out, bad)  # no coercion when disabled
            self.assertEqual(self.captured, [])
        finally:
            os.environ["CONTRACT_VALIDATOR_ENABLE"] = "1"


class TestProviderGenerateOpenRouterPath(_IntegrationBase):
    """
    Monkey-patch the OpenRouter pipeline to inject a known JSON string, then
    assert ``_provider_generate`` runs validation + coercion. Cross-checks
    against real network calls are irrelevant — we're proving the validator is
    plumbed at the single return point.
    """

    def _install_fake_pool(self, json_content: str) -> None:
        # Build a minimal pool that sync_failover_execute can chew through.
        fake_completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json_content))]
        )

        class _FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **kw: fake_completion,
                    )
                )

        class _FakePool:
            def sync_clients_for_request(self, _flag):
                return [_FakeClient()]

        self.client._openrouter_pool = _FakePool()
        self.client._provider = "openrouter"

        # sync_failover_execute is already imported at top of llm_client. We
        # override it module-scope so it just invokes the callable once.
        def _fake_failover(clients, fn, **_kw):
            try:
                return fn(clients[0]), None
            except Exception as e:  # pragma: no cover - defensive
                return None, e

        self._orig_failover = lc.sync_failover_execute
        lc.sync_failover_execute = _fake_failover

    def tearDown(self) -> None:
        if hasattr(self, "_orig_failover"):
            lc.sync_failover_execute = self._orig_failover
        super().tearDown()

    def test_valid_openrouter_response_passes_through(self) -> None:
        self._install_fake_pool(
            '{"headline": "hot chip boom", "key_points": ["a","b"], "confidence": 0.8}'
        )
        result, version = self.client._provider_generate("bull", "prompt")
        self.assertEqual(result["headline"], "hot chip boom")
        self.assertEqual(version, "1.0.0")
        self.assertEqual(self.captured, [])

    def test_invalid_openrouter_response_coerces_to_fallback(self) -> None:
        # Model returns a structurally wrong payload (missing required keys).
        self._install_fake_pool('{"headline": "only headline"}')
        result, version = self.client._provider_generate("bull", "prompt")
        # Validator should have coerced to the seeded fallback.
        self.assertEqual(result, BULL_FALLBACK)
        self.assertEqual(version, "1.0.0")
        self.assertTrue(self.captured)
        codes = {v.code for v, _ctx in self.captured}
        self.assertIn("missing_required", codes)


if __name__ == "__main__":
    unittest.main()
