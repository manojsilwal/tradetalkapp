"""
Tests for the Gemini-primary routing (GEMINI_PRIMARY=1).

Behavior under test:

  1. :func:`backend.gemini_llm.resolve_gemini_model` returns the right model for
     each tier ("heavy" → :data:`GEMINI_MODEL`, "light" → :data:`GEMINI_MODEL_LIGHT`).
  2. :func:`backend.llm_client._gemini_model_for_role` picks the heavy model for
     bull/bear/moderator/strategy_parser and the light model for swarm_analyst/
     swarm_synthesizer/rag_narrative_polish/video_veo_text_fallback.
  3. With ``GEMINI_PRIMARY=1`` and a Gemini key set:
     - :meth:`LLMClient._provider_generate` calls Gemini FIRST and never touches
       the OpenRouter path (even if a pool is configured).
     - When Gemini returns an empty string / raises, it falls back to the
       rule-based :data:`FALLBACK_TEMPLATES`, NOT to OpenRouter.
     - :meth:`LLMClient._plain_text_generate_sync` follows the same contract
       and returns the original ``user`` text on Gemini failure.
  4. Without ``GEMINI_PRIMARY=1`` the old OpenRouter-primary path is preserved
     (no regression).

All Gemini calls are monkey-patched — no network, no API key needed for the
happy-path assertions (we stub ``gemini_usable_for_chat`` too).
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock


class TestResolveGeminiModel(unittest.TestCase):
    def test_heavy_and_light(self):
        from backend.gemini_llm import (
            GEMINI_MODEL,
            GEMINI_MODEL_LIGHT,
            resolve_gemini_model,
        )

        self.assertEqual(resolve_gemini_model("heavy"), GEMINI_MODEL)
        self.assertEqual(resolve_gemini_model("light"), GEMINI_MODEL_LIGHT)

    def test_default_and_unknown_default_to_heavy(self):
        from backend.gemini_llm import GEMINI_MODEL, resolve_gemini_model

        self.assertEqual(resolve_gemini_model(), GEMINI_MODEL)
        self.assertEqual(resolve_gemini_model("totally-made-up"), GEMINI_MODEL)


class TestGeminiModelForRole(unittest.TestCase):
    def test_heavy_roles_get_heavy_model(self):
        from backend.gemini_llm import GEMINI_MODEL
        from backend.llm_client import _gemini_model_for_role

        for role in ("bull", "bear", "moderator", "strategy_parser", "gold_advisor"):
            self.assertEqual(
                _gemini_model_for_role(role),
                GEMINI_MODEL,
                f"{role} should map to GEMINI_MODEL",
            )

    def test_light_roles_get_light_model(self):
        from backend.gemini_llm import GEMINI_MODEL_LIGHT
        from backend.llm_client import _gemini_model_for_role

        for role in (
            "swarm_analyst",
            "swarm_synthesizer",
            "swarm_reflection_writer",
            "rag_narrative_polish",
            "video_scene_director",
            "video_veo_text_fallback",
        ):
            self.assertEqual(
                _gemini_model_for_role(role),
                GEMINI_MODEL_LIGHT,
                f"{role} should map to GEMINI_MODEL_LIGHT",
            )

    def test_unknown_role_defaults_to_heavy(self):
        from backend.gemini_llm import GEMINI_MODEL
        from backend.llm_client import _gemini_model_for_role

        self.assertEqual(_gemini_model_for_role("not_a_role"), GEMINI_MODEL)


class _EnvSandbox(unittest.TestCase):
    """Save/restore env between tests so GEMINI_PRIMARY flips don't leak."""

    _MANAGED = (
        "GEMINI_PRIMARY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_LLM_FALLBACK",
        "OPENROUTER_API_KEY",
    )

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._MANAGED}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestProviderGenerateGeminiPrimary(_EnvSandbox):
    """``GEMINI_PRIMARY=1`` must route ``_provider_generate`` through Gemini."""

    def _mk_client_with_mock_openrouter(self):
        from backend.llm_client import LLMClient

        client = LLMClient()
        # Simulate a configured OpenRouter pool so we can prove we DON'T call it.
        mock_pool = mock.MagicMock()
        mock_pool.sync_clients_for_request = mock.MagicMock(return_value=[])
        client._openrouter_pool = mock_pool
        return client, mock_pool

    def test_primary_calls_gemini_and_skips_openrouter(self):
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "test-key"

        client, mock_pool = self._mk_client_with_mock_openrouter()

        captured: dict = {}

        def fake_gemini_sync(*, system, user, max_tokens, temperature, json_mode, model):
            captured["model"] = model
            captured["system"] = system
            captured["user"] = user
            captured["json_mode"] = json_mode
            return json.dumps(
                {"headline": "from-gemini", "key_points": ["a", "b", "c"], "confidence": 0.7}
            )

        with mock.patch(
            "backend.llm_client.gemini_simple_completion_sync",
            side_effect=fake_gemini_sync,
        ):
            result, version = client._provider_generate("bull", "Is AAPL a buy?")

        self.assertEqual(result.get("headline"), "from-gemini")
        self.assertEqual(captured["json_mode"], True)
        # Bull = heavy tier → GEMINI_MODEL (pro).
        from backend.gemini_llm import GEMINI_MODEL

        self.assertEqual(captured["model"], GEMINI_MODEL)
        # OpenRouter path must NOT have been touched.
        mock_pool.sync_clients_for_request.assert_not_called()

    def test_primary_light_role_uses_flash_model(self):
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "test-key"

        client, _ = self._mk_client_with_mock_openrouter()

        captured: dict = {}

        def fake_gemini_sync(*, system, user, max_tokens, temperature, json_mode, model):
            captured["model"] = model
            return json.dumps({"signal": 1, "rationale": "x", "confidence": 0.6})

        with mock.patch(
            "backend.llm_client.gemini_simple_completion_sync",
            side_effect=fake_gemini_sync,
        ):
            result, _ = client._provider_generate("swarm_analyst", "factor=momentum")

        from backend.gemini_llm import GEMINI_MODEL_LIGHT

        self.assertEqual(captured["model"], GEMINI_MODEL_LIGHT)
        self.assertEqual(result.get("signal"), 1)

    def test_primary_gemini_failure_returns_template_not_openrouter(self):
        """When Gemini errors, fall back to FALLBACK_TEMPLATES — never OpenRouter."""
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "test-key"

        client, mock_pool = self._mk_client_with_mock_openrouter()

        def fake_gemini_sync(**kwargs):
            raise RuntimeError("simulated Gemini 503")

        from backend.llm_client import FALLBACK_TEMPLATES

        with mock.patch(
            "backend.llm_client.gemini_simple_completion_sync",
            side_effect=fake_gemini_sync,
        ):
            result, _ = client._provider_generate("bull", "prompt")

        self.assertEqual(result, FALLBACK_TEMPLATES["bull"])
        mock_pool.sync_clients_for_request.assert_not_called()

    def test_primary_gemini_empty_returns_template_not_openrouter(self):
        """Empty Gemini output is treated as a failure; template wins, not OR."""
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "test-key"

        client, mock_pool = self._mk_client_with_mock_openrouter()

        from backend.llm_client import FALLBACK_TEMPLATES

        with mock.patch(
            "backend.llm_client.gemini_simple_completion_sync",
            return_value="",  # empty string = failure
        ):
            result, _ = client._provider_generate("bear", "prompt")

        self.assertEqual(result, FALLBACK_TEMPLATES["bear"])
        mock_pool.sync_clients_for_request.assert_not_called()

    def test_primary_off_keeps_openrouter_primary(self):
        """With GEMINI_PRIMARY cleared, the old OpenRouter-primary path runs."""
        os.environ.pop("GEMINI_PRIMARY", None)
        # No Gemini key either — exclusively exercise the OR path.
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)

        client, mock_pool = self._mk_client_with_mock_openrouter()

        # No Gemini and no OR clients → falls through to FALLBACK_TEMPLATES, and
        # critically the OR pool.sync_clients_for_request IS invoked (proving
        # we took the OpenRouter branch).
        with mock.patch(
            "backend.llm_client.gemini_simple_completion_sync",
            side_effect=AssertionError("Gemini must not be called when primary=off and no key"),
        ):
            result, _ = client._provider_generate("bull", "prompt")

        mock_pool.sync_clients_for_request.assert_called()
        from backend.llm_client import FALLBACK_TEMPLATES

        self.assertEqual(result, FALLBACK_TEMPLATES["bull"])


class TestPlainTextGenerateGeminiPrimary(_EnvSandbox):
    def test_primary_uses_gemini_light_and_skips_openrouter(self):
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "test-key"

        from backend.llm_client import LLMClient

        client = LLMClient()
        mock_pool = mock.MagicMock()
        mock_pool.sync_clients_for_request = mock.MagicMock(return_value=[])
        client._openrouter_pool = mock_pool

        captured: dict = {}

        def fake_gemini_sync(*, system, user, max_tokens, temperature, json_mode, model):
            captured["model"] = model
            captured["json_mode"] = json_mode
            return "A" * 50  # >40 chars so the caller treats it as valid output

        with mock.patch(
            "backend.llm_client.gemini_simple_completion_sync",
            side_effect=fake_gemini_sync,
        ):
            out = client._plain_text_generate_sync("polish this", "draft text")

        self.assertEqual(len(out), 50)
        self.assertEqual(captured["json_mode"], False)
        from backend.gemini_llm import GEMINI_MODEL_LIGHT

        self.assertEqual(captured["model"], GEMINI_MODEL_LIGHT)
        mock_pool.sync_clients_for_request.assert_not_called()

    def test_primary_gemini_failure_returns_user_text(self):
        """When Gemini fails on the plain-text path, the untouched user string
        is returned — exactly what an OpenRouter outage already does today."""
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "test-key"

        from backend.llm_client import LLMClient

        client = LLMClient()
        mock_pool = mock.MagicMock()
        client._openrouter_pool = mock_pool

        with mock.patch(
            "backend.llm_client.gemini_simple_completion_sync",
            side_effect=RuntimeError("Gemini down"),
        ):
            out = client._plain_text_generate_sync("system", "the draft")

        self.assertEqual(out, "the draft")
        mock_pool.sync_clients_for_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
