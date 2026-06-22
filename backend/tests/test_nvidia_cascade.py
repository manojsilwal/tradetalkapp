"""Tests for the NVIDIA -> OpenRouter -> Gemini provider cascade routing."""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock
from types import SimpleNamespace

from backend.data_errors import InsufficientDataError
from backend.openrouter_pool import (
    collect_nvidia_llm_api_keys,
    collect_openrouter_api_keys,
    resolve_llm_http_provider,
)


class TestProviderKeysAndResolution(unittest.TestCase):
    def setUp(self):
        self._orig_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)

    def test_collect_nvidia_keys(self):
        os.environ["NVIDIA_API_KEY"] = "nv-1"
        os.environ["NVIDIA_API_KEY_2"] = "nv-2"
        self.assertEqual(collect_nvidia_llm_api_keys(), ["nv-1", "nv-2"])

        os.environ.pop("NVIDIA_API_KEY", None)
        os.environ.pop("NVIDIA_API_KEY_2", None)
        os.environ["LLM_HTTP_PROVIDER"] = "nvidia"
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "nv-fallback"
        self.assertEqual(collect_nvidia_llm_api_keys(), ["nv-fallback"])

    def test_resolve_provider(self):
        os.environ.pop("NVIDIA_API_KEY_2", None)
        os.environ.pop("OPENROUTER_API_KEY_2", None)

        os.environ["NVIDIA_API_KEY"] = "nv-key"
        os.environ["OPENROUTER_API_KEY"] = "or-key"
        self.assertEqual(resolve_llm_http_provider(), "nvidia")

        os.environ.pop("NVIDIA_API_KEY", None)
        self.assertEqual(resolve_llm_http_provider(), "openrouter")

        os.environ.pop("OPENROUTER_API_KEY", None)
        self.assertEqual(resolve_llm_http_provider(), "none")


class TestLLMClientCascadeRouting(unittest.TestCase):
    def setUp(self):
        self._orig_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)

    @mock.patch("backend.llm_client.collect_nvidia_llm_api_keys", return_value=["nv-key"])
    @mock.patch("backend.llm_client.collect_openrouter_api_keys", return_value=["or-key"])
    @mock.patch("backend.llm_client.get_or_create_llm_openai_compatible_pool")
    @mock.patch("backend.llm_client.get_or_create_openrouter_pool")
    def test_client_init_pools(self, mock_or_pool, mock_nv_pool, mock_collect_or, mock_collect_nv):
        from backend.llm_client import LLMClient
        # Configure mocks to return something non-None
        mock_or_pool.return_value = mock.Mock()
        mock_nv_pool.return_value = mock.Mock()
        
        client = LLMClient()
        self.assertIsNotNone(client._nvidia_pool)
        self.assertIsNotNone(client._openrouter_pool)
        self.assertEqual(client._provider, "nvidia")
        self.assertEqual(client._endpoint, "https://integrate.api.nvidia.com/v1")

    @mock.patch("backend.llm_client.collect_nvidia_llm_api_keys", return_value=["nv-key"])
    @mock.patch("backend.llm_client.collect_openrouter_api_keys", return_value=["or-key"])
    def test_provider_generate_nvidia_success(self, mock_collect_or, mock_collect_nv):
        from backend.llm_client import LLMClient
        client = LLMClient()

        # Mock pool clients
        fake_nv_completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"headline": "nv-hit", "key_points": ["a"], "confidence": 0.9}'))],
            usage=None
        )
        fake_nv_pool = mock.Mock()
        fake_nv_pool.sync_clients_for_request.return_value = [mock.Mock()]
        client._nvidia_pool = fake_nv_pool

        fake_or_pool = mock.Mock()
        client._openrouter_pool = fake_or_pool

        with mock.patch("backend.llm_client.sync_failover_execute", return_value=(fake_nv_completion, None)) as mock_execute:
            res, _version = client._provider_generate("bull", "prompt")
            self.assertEqual(res["headline"], "nv-hit")
            mock_execute.assert_called_once()
            # Verify OpenRouter sync_clients_for_request was NOT called because Nvidia succeeded
            fake_or_pool.sync_clients_for_request.assert_not_called()

    @mock.patch("backend.llm_client.collect_nvidia_llm_api_keys", return_value=["nv-key"])
    @mock.patch("backend.llm_client.collect_openrouter_api_keys", return_value=["or-key"])
    def test_provider_generate_nvidia_fails_openrouter_succeeds(self, mock_collect_or, mock_collect_nv):
        from backend.llm_client import LLMClient
        client = LLMClient()

        fake_nv_pool = mock.Mock()
        fake_nv_pool.sync_clients_for_request.return_value = [mock.Mock()]
        client._nvidia_pool = fake_nv_pool

        fake_or_completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"headline": "or-hit", "key_points": ["a"], "confidence": 0.8}'))],
            usage=None
        )
        fake_or_pool = mock.Mock()
        fake_or_pool.sync_clients_for_request.return_value = [mock.Mock()]
        client._openrouter_pool = fake_or_pool

        # First call fails (Nvidia), second call succeeds (OpenRouter)
        side_effects = [
            (None, RuntimeError("NVIDIA 503")),
            (fake_or_completion, None)
        ]

        with mock.patch("backend.llm_client.sync_failover_execute", side_effect=side_effects):
            res, _version = client._provider_generate("bull", "prompt")
            self.assertEqual(res["headline"], "or-hit")

    @mock.patch("backend.llm_client.collect_nvidia_llm_api_keys", return_value=["nv-key"])
    @mock.patch("backend.llm_client.collect_openrouter_api_keys", return_value=["or-key"])
    @mock.patch("backend.llm_client.gemini_llm_fallback_enabled", return_value=True)
    def test_provider_generate_http_fails_gemini_succeeds(self, mock_fallback_enabled, mock_collect_or, mock_collect_nv):
        from backend.llm_client import LLMClient
        client = LLMClient()

        fake_nv_pool = mock.Mock()
        fake_nv_pool.sync_clients_for_request.return_value = [mock.Mock()]
        client._nvidia_pool = fake_nv_pool

        fake_or_pool = mock.Mock()
        fake_or_pool.sync_clients_for_request.return_value = [mock.Mock()]
        client._openrouter_pool = fake_or_pool

        gemini_result = {"headline": "gemini-hit", "key_points": ["a"], "confidence": 0.95}

        # Both HTTP calls fail
        side_effects = [
            (None, RuntimeError("NVIDIA 503")),
            (None, RuntimeError("OpenRouter 429"))
        ]

        with mock.patch("backend.llm_client.sync_failover_execute", side_effect=side_effects), mock.patch.object(
            client, "_gemini_try_json_role", return_value=gemini_result
        ) as mock_gemini:
            res, _version = client._provider_generate("bull", "prompt")
            self.assertEqual(res["headline"], "gemini-hit")
            mock_gemini.assert_called_once()

    @mock.patch("backend.llm_client.collect_nvidia_llm_api_keys", return_value=["nv-key"])
    @mock.patch("backend.llm_client.collect_openrouter_api_keys", return_value=["or-key"])
    @mock.patch("backend.llm_client.gemini_llm_fallback_enabled", return_value=True)
    def test_provider_generate_all_fails_verdict_role_raises(self, mock_fallback_enabled, mock_collect_or, mock_collect_nv):
        from backend.llm_client import LLMClient
        client = LLMClient()

        fake_nv_pool = mock.Mock()
        fake_nv_pool.sync_clients_for_request.return_value = [mock.Mock()]
        client._nvidia_pool = fake_nv_pool

        fake_or_pool = mock.Mock()
        fake_or_pool.sync_clients_for_request.return_value = [mock.Mock()]
        client._openrouter_pool = fake_or_pool

        side_effects = [
            (None, RuntimeError("NVIDIA 503")),
            (None, RuntimeError("OpenRouter 503"))
        ]

        with mock.patch("backend.llm_client.sync_failover_execute", side_effect=side_effects), mock.patch.object(
            client, "_gemini_try_json_role", return_value=None
        ):
            with self.assertRaises(InsufficientDataError):
                client._provider_generate("bull", "prompt")


if __name__ == "__main__":
    unittest.main()
