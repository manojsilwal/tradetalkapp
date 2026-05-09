"""
Offline contract test for the learning-video pipeline (scene director + Veo client).

Mocks Gemini JSON and the Genai Veo client so the default suite never uses the network.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from google.genai import types as gtypes

from backend.llm_client import LLMClient
from backend.video_generation_agent import VIDEO_VEO_MODEL, _veo_duration_seconds

_MP4_SIGNATURE = b"ftyp"


class _MockVideoFile:
    def save(self, path: str) -> None:
        blob = (
            b"\x00\x00\x00\x20ftypmp41\x00\x00\x00\x00mp41isom"
            + b"\x00" * 50_000
        )
        with open(path, "wb") as f:
            f.write(blob)


class _MockGeneratedVideo:
    video = _MockVideoFile()


class _MockResponse:
    generated_videos = [_MockGeneratedVideo()]


class _MockOperation:
    done = True
    error = None
    response = _MockResponse()


def _veo_mock_client() -> MagicMock:
    mock_client = MagicMock()
    mock_client.models.generate_videos.return_value = _MockOperation()
    mock_client.operations.get.return_value = _MockOperation()
    mock_client.files.download.return_value = None
    return mock_client


class TestShortLearningVideoOffline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_primary = os.environ.get("GEMINI_PRIMARY")
        cls._saved_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_PRIMARY"] = "1"
        os.environ["GEMINI_API_KEY"] = "offline-video-contract-key"

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

    def test_short_learning_video_generates_mp4(self) -> None:
        scene_payload = {
            "scenes": [
                {
                    "scene": 1,
                    "visual_prompt": "abstract 2D finance chart motion, no text",
                    "caption": "What is a Moving Average?",
                    "duration": 4,
                }
            ]
        }
        veo_client = _veo_mock_client()
        out_dir = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                os.pardir,
                "static",
                "videos",
                "pytest_smoke",
            )
        )
        os.makedirs(out_dir, exist_ok=True)

        async def _run() -> str:
            with patch(
                "backend.llm_client.gemini_simple_completion_sync",
                return_value=json.dumps(scene_payload),
            ):
                llm = LLMClient()
                llm._provider = "openrouter"
                prompt = (
                    "Lesson topic: What is a Moving Average?\n"
                    "Track: Technical Analysis\n"
                    "Level: Beginner\n"
                    "Produce EXACTLY ONE scene."
                )
                payload = await llm.generate("video_scene_director", prompt)
                self.assertIsInstance(payload, dict)
                scenes = payload.get("scenes") or []
                self.assertGreaterEqual(len(scenes), 1)
                scene0 = scenes[0]
                visual_prompt = str(scene0.get("visual_prompt") or "").strip()
                self.assertGreater(len(visual_prompt), 10)

            duration = _veo_duration_seconds()
            operation: Any = veo_client.models.generate_videos(
                model=VIDEO_VEO_MODEL,
                prompt=visual_prompt,
                config=gtypes.GenerateVideosConfig(
                    aspect_ratio="9:16",
                    resolution="720p",
                    number_of_videos=1,
                    duration_seconds=duration,
                ),
            )
            self.assertTrue(operation.done)
            err = getattr(operation, "error", None)
            self.assertIsNone(err)
            video = operation.response.generated_videos[0]
            ts = int(time.time())
            out_path = os.path.join(out_dir, f"learning_short_offline_{ts}.mp4")
            veo_client.files.download(file=video.video)
            video.video.save(out_path)
            return out_path

        out_path = asyncio.run(_run())

        self.assertTrue(os.path.isfile(out_path))
        size = os.path.getsize(out_path)
        self.assertGreater(size, 10_000)
        with open(out_path, "rb") as f:
            header = f.read(16)
        self.assertIn(_MP4_SIGNATURE, header)


if __name__ == "__main__":
    unittest.main()
