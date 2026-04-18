"""
LIVE end-to-end smoke test for the TradeTalk learning-video pipeline.

Generates ONE short (4-second) learning video clip via Google Veo — the same
pipeline Academy lessons use, just narrowed to a single scene so the test is
cheap and finishes in ~60-120s. Output is written to::

    backend/static/videos/pytest_smoke/learning_short_<timestamp>.mp4

The test asserts:

  1. The LLM (Gemini-light with ``GEMINI_PRIMARY=1``) produces a usable
     ``visual_prompt`` for the scene via the ``video_scene_director`` role.
  2. Google Veo accepts the prompt and returns an MP4 within the polling window.
  3. The MP4 is saved to disk, is non-trivial in size (>10 KB), and the file
     header is a valid MP4 container (starts with the ISO base-media signature).

Skipped by default. To run::

    export GEMINI_API_KEY=your_key        # or set in backend/.env
    export RUN_VEO_LIVE_SMOKE=1
    python -m pytest backend/tests/test_video_live_smoke.py -v -s

Cost is 1x Veo-Lite clip (cheap tier) plus one Gemini-Flash call. It's the
smallest unit of proof that the full Academy video stack works end-to-end.
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
from typing import Any


# ── Env bootstrap (reuse the same .env loader pattern as the LLM smoke) ─────


def _load_backend_dotenv_once() -> None:
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, ".env")
    )
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        return


_RUN_FLAG = os.environ.get("RUN_VEO_LIVE_SMOKE", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
if _RUN_FLAG:
    _load_backend_dotenv_once()


def _has_gemini_key() -> bool:
    return bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )


_SKIP_REASON = (
    "live Veo smoke disabled — set RUN_VEO_LIVE_SMOKE=1 and either "
    "GEMINI_API_KEY or GOOGLE_API_KEY to enable (this will call paid Veo "
    "and take ~60-120s)"
)


# MP4 files start with an ``ftyp`` ISO Base Media box. The first 4 bytes are
# the box size; bytes 4-7 are the ``ftyp`` fourCC. We check bytes 4-7 for
# "ftyp" since that's the stable ISO signature across Veo's encoder settings.
_MP4_SIGNATURE = b"ftyp"


@unittest.skipUnless(_RUN_FLAG and _has_gemini_key(), _SKIP_REASON)
class TestShortLearningVideoE2E(unittest.TestCase):
    """One short learning video, end-to-end, with real Gemini + Veo calls."""

    @classmethod
    def setUpClass(cls):
        cls._saved_primary = os.environ.get("GEMINI_PRIMARY")
        os.environ["GEMINI_PRIMARY"] = "1"

        cls.out_dir = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                os.pardir,
                "static",
                "videos",
                "pytest_smoke",
            )
        )
        os.makedirs(cls.out_dir, exist_ok=True)

        from backend.video_generation_agent import VIDEO_VEO_MODEL

        cls.veo_model = VIDEO_VEO_MODEL
        print(
            f"\n[veo-smoke] GEMINI_PRIMARY=1 veo_model={cls.veo_model} "
            f"out_dir={cls.out_dir}"
        )

    @classmethod
    def tearDownClass(cls):
        if cls._saved_primary is None:
            os.environ.pop("GEMINI_PRIMARY", None)
        else:
            os.environ["GEMINI_PRIMARY"] = cls._saved_primary

    def test_short_learning_video_generates_mp4(self):
        from backend.llm_client import get_llm_client
        from backend.video_generation_agent import (
            VIDEO_VEO_MODEL,
            _build_genai_client,
            _veo_duration_seconds,
        )

        # ── Step 1. Generate the storyboard via the same Gemini path the real
        # lesson pipeline uses. Ask for just one scene so the test stays cheap.
        llm = get_llm_client()
        topic = "What is a Moving Average?"
        track = "Technical Analysis"
        level = "Beginner"

        prompt = (
            f"Lesson topic: {topic}\n"
            f"Track: {track}\n"
            f"Level: {level}\n"
            f"Produce EXACTLY ONE scene (scenes array of length 1) with a "
            f"concise caption and an animation-friendly visual_prompt "
            f"(abstract 2D motion graphics, no people, no text). "
            f"Keep it suitable for a short 4-second clip."
        )

        async def _call_scene_director() -> dict:
            return await llm.generate("video_scene_director", prompt)

        payload = asyncio.run(_call_scene_director())
        self.assertIsInstance(payload, dict, f"unexpected payload type: {payload!r}")
        scenes = payload.get("scenes") or []
        self.assertGreaterEqual(
            len(scenes),
            1,
            f"scene director returned no scenes: {payload!r}",
        )

        scene = scenes[0]
        visual_prompt = str(scene.get("visual_prompt") or "").strip()
        caption = str(scene.get("caption") or topic).strip()
        self.assertGreater(
            len(visual_prompt),
            10,
            f"visual_prompt too short: {visual_prompt!r}",
        )
        print(
            f"[veo-smoke] scene caption={caption[:80]!r} "
            f"visual_prompt={visual_prompt[:120]!r}"
        )

        # ── Step 2. Call Veo directly (same SDK path as the production agent,
        # just inlined here so we don't have to generate 8 scenes).
        from google.genai import types as gtypes

        client = _build_genai_client()
        duration = _veo_duration_seconds()

        operation: Any = client.models.generate_videos(
            model=VIDEO_VEO_MODEL,
            prompt=visual_prompt,
            config=gtypes.GenerateVideosConfig(
                aspect_ratio="9:16",
                resolution="720p",
                number_of_videos=1,
                duration_seconds=duration,
            ),
        )

        # ── Step 3. Poll for completion (Veo is async). Cap total wait at
        # ~10 minutes — longer than expected, but safer than a flaky timeout.
        max_polls = 60
        polls = 0
        poll_interval = 10
        t0 = time.monotonic()
        while not operation.done and polls < max_polls:
            time.sleep(poll_interval)
            operation = client.operations.get(operation)
            polls += 1
            print(
                f"[veo-smoke] poll {polls}/{max_polls} "
                f"done={operation.done} elapsed={time.monotonic() - t0:.0f}s"
            )

        self.assertTrue(
            operation.done,
            f"Veo operation did not complete within {max_polls * poll_interval}s",
        )

        err = getattr(operation, "error", None)
        self.assertIsNone(err, f"Veo operation error: {err}")

        self.assertTrue(
            operation.response and getattr(operation.response, "generated_videos", None),
            f"Veo returned no generated_videos: response={operation.response!r}",
        )

        # ── Step 4. Save the first clip and verify it's a real MP4.
        ts = int(time.time())
        out_path = os.path.join(self.out_dir, f"learning_short_{ts}.mp4")
        video = operation.response.generated_videos[0]
        client.files.download(file=video.video)
        video.video.save(out_path)

        self.assertTrue(
            os.path.isfile(out_path),
            f"output MP4 was not written to {out_path}",
        )
        size = os.path.getsize(out_path)
        self.assertGreater(
            size,
            10_000,
            f"output MP4 suspiciously small ({size} bytes) — likely corrupt",
        )

        with open(out_path, "rb") as f:
            header = f.read(16)
        self.assertIn(
            _MP4_SIGNATURE,
            header,
            f"output missing MP4 'ftyp' signature; header={header!r}",
        )
        print(
            f"[veo-smoke] OK topic={topic!r} caption={caption[:60]!r} "
            f"bytes={size} path={out_path}"
        )


if __name__ == "__main__":
    unittest.main()
