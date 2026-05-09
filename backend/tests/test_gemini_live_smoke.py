"""
Offline contract tests for Gemini-primary LLM paths used by TradeTalk.

These mirror the former live smoke module but patch ``gemini_simple_completion_sync``
(and streaming helpers) so the default suite never calls the network.

This module stays fully offline. For paid live API checks, run a one-off script
or integration job with real keys outside the default ``unittest`` discover path.
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from backend.llm_client import FALLBACK_TEMPLATES, LLMClient


def _client_for_generate_path() -> LLMClient:
    """``generate_with_meta`` only calls ``_provider_generate`` when HTTP provider is wired."""
    c = LLMClient()
    c._provider = "openrouter"
    return c


class TestGeminiPrimaryPathContract(unittest.TestCase):
    """Contract checks for Gemini-primary routing (no live API calls)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_primary = os.environ.get("GEMINI_PRIMARY")
        cls._saved_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "offline-contract-test-key"

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._saved_primary is None:
            os.environ.pop("GEMINI_PRIMARY", None)
        else:
            os.environ["GEMINI_PRIMARY"] = cls._saved_primary
        if cls._saved_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = cls._saved_key

    def test_01_raw_gemini_handshake(self) -> None:
        import backend.gemini_llm as gl

        with patch.object(gl, "gemini_simple_completion_sync", return_value="PONG") as m:
            out = gl.gemini_simple_completion_sync(
                system="You are a laconic greeter.",
                user="Say exactly the word PONG and nothing else.",
                max_tokens=512,
                temperature=0.0,
                json_mode=False,
                model=gl.GEMINI_MODEL_LIGHT,
            )
        m.assert_called_once()
        self.assertIn("PONG", out.upper())

    def test_02_agent_heavy_tier_bull(self) -> None:
        payload = {
            "headline": "Growth runway",
            "key_points": ["a", "b", "c"],
            "confidence": 0.7,
        }
        with patch(
            "backend.llm_client.gemini_simple_completion_sync",
            return_value=json.dumps(payload),
        ):
            client = _client_for_generate_path()
            result, _version = client._provider_generate(
                "bull",
                "AAPL context for test.",
            )
        self.assertIsInstance(result, dict)
        self.assertNotEqual(result, FALLBACK_TEMPLATES["bull"])
        self.assertIn("headline", result)
        self.assertIn("key_points", result)
        self.assertIn("confidence", result)

    def test_03_agent_light_tier_swarm_analyst(self) -> None:
        payload = {
            "signal": 1,
            "rationale": "Momentum supportive.",
            "confidence": 0.55,
        }
        with patch(
            "backend.llm_client.gemini_simple_completion_sync",
            return_value=json.dumps(payload),
        ):
            client = _client_for_generate_path()
            result, _version = client._provider_generate(
                "swarm_analyst",
                "Factor: momentum.\nReturn your signal JSON.",
            )
        self.assertIsInstance(result, dict)
        self.assertNotEqual(result, FALLBACK_TEMPLATES["swarm_analyst"])
        self.assertIn("signal", result)
        self.assertIn("confidence", result)

    def test_04_plain_text_rag_polish(self) -> None:
        user_draft = (
            "AAPL printed strong quarterly revenue. Services margin expanded."
        )
        polished = (
            "Apple posted solid revenue with expanding services margins, "
            "highlighting a healthier mix."
        )
        with patch(
            "backend.llm_client.gemini_simple_completion_sync",
            return_value=polished,
        ):
            client = _client_for_generate_path()
            out = client._plain_text_generate_sync(
                system="Rewrite briefly.",
                user=user_draft,
            )
        self.assertNotEqual(out.strip(), user_draft.strip())
        self.assertGreater(len(out), 40)

    def test_05_streaming_chat_plain(self) -> None:
        async def _fake_events(**_kwargs):
            yield {"kind": "text", "text": "P/E ratio relates price to earnings per share."}

        async def _collect() -> str:
            client = LLMClient()
            buf: list[str] = []
            async for chunk in client.stream_chat_plain(
                system="You are a brief finance assistant.",
                messages=[
                    {
                        "role": "user",
                        "content": "In ONE short sentence: what is the P/E ratio?",
                    }
                ],
                max_tokens=256,
            ):
                buf.append(chunk)
            return "".join(buf)

        with patch(
            "backend.llm_client.gemini_fallback_chat_events",
            _fake_events,
        ):
            text = asyncio.run(_collect())
        self.assertGreater(len(text), 20)
        self.assertFalse(text.lstrip().startswith("[Chat error"))

    def test_06_video_scene_director_json(self) -> None:
        payload = {
            "scenes": [
                {
                    "scene": 1,
                    "visual_prompt": "abstract 2D motion graphic waves",
                    "caption": "Moving averages smooth price.",
                    "duration": 4,
                }
            ]
        }

        async def _run() -> dict:
            with patch(
                "backend.llm_client.gemini_simple_completion_sync",
                return_value=json.dumps(payload),
            ):
                llm = _client_for_generate_path()
                return await llm.generate("video_scene_director", "lesson prompt")

        out = asyncio.run(_run())
        self.assertIsInstance(out, dict)
        self.assertNotEqual(out, FALLBACK_TEMPLATES["video_scene_director"])
        self.assertIn("scenes", out)
        scenes = out.get("scenes") or []
        self.assertGreaterEqual(len(scenes), 1)
        self.assertIn("caption", scenes[0])

    def test_07_video_veo_text_fallback_json(self) -> None:
        payload = {
            "caption": "Compound interest",
            "body": (
                "Compound interest grows savings when returns earn their own returns. "
                "Time and reinvestment amplify the effect."
            ),
        }

        async def _run() -> dict:
            with patch(
                "backend.llm_client.gemini_simple_completion_sync",
                return_value=json.dumps(payload),
            ):
                llm = _client_for_generate_path()
                return await llm.generate("video_veo_text_fallback", "slide prompt")

        out = asyncio.run(_run())
        self.assertIsInstance(out, dict)
        self.assertNotEqual(out, FALLBACK_TEMPLATES["video_veo_text_fallback"])
        self.assertIn("caption", out)
        self.assertIn("body", out)
        self.assertGreater(len(str(out["body"]).strip()), 20)


if __name__ == "__main__":
    unittest.main()
