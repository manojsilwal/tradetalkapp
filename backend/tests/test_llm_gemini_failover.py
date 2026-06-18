"""OpenRouter → Gemini failover when OpenRouter returns unparseable JSON."""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("GEMINI_PRIMARY", "0")
os.environ.setdefault("GEMINI_LLM_FALLBACK", "1")


class TestOpenRouterJsonFailover(unittest.TestCase):
    def test_unparseable_openrouter_json_tries_gemini_for_verdict_role(self) -> None:
        from backend.llm_client import LLMClient

        client = LLMClient()
        gemini_result = {"signal": 1, "rationale": "via gemini", "confidence": 0.7}

        fake_completion = mock.Mock()
        fake_completion.choices = [mock.Mock(message=mock.Mock(content="not json at all"))]
        fake_completion.usage = None

        fake_pool = mock.Mock()
        fake_pool.sync_clients_for_request.return_value = [mock.Mock()]

        with mock.patch.object(client, "_openrouter_pool", fake_pool), mock.patch(
            "backend.llm_client.sync_failover_execute",
            return_value=(fake_completion, None),
        ), mock.patch(
            "backend.llm_client.gemini_llm_fallback_enabled",
            return_value=True,
        ), mock.patch.object(
            client,
            "_gemini_try_json_role",
            return_value=gemini_result,
        ) as gemini_try:
            result, _version = client._provider_generate(
                "swarm_analyst", "factor prompt for AAPL"
            )

        self.assertEqual(result, gemini_result)
        gemini_try.assert_called_once()

    def test_verdict_role_parse_failure_returns_none(self) -> None:
        from backend.llm_client import LLMClient

        client = LLMClient()
        self.assertIsNone(client._parse_json_response("plain prose only", "swarm_analyst"))

    def test_non_verdict_role_parse_failure_uses_template(self) -> None:
        from backend.llm_client import LLMClient

        client = LLMClient()
        out = client._parse_json_response("plain prose only", "video_scene_director")
        self.assertEqual(out, {"scenes": []})


if __name__ == "__main__":
    unittest.main()
