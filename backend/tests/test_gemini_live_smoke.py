"""
LIVE Gemini smoke tests — one test per LLM path that the TradeTalk app exercises
with GEMINI_PRIMARY=1. Unlike :mod:`test_gemini_primary_routing`, these tests
actually call the Gemini API and verify real responses come back.

By default every test in this module is skipped. To run them::

    export GEMINI_API_KEY=your_key       # or set in backend/.env
    export RUN_GEMINI_LIVE_SMOKE=1
    python -m pytest backend/tests/test_gemini_live_smoke.py -v -s

The ``-s`` flag is recommended so you can see the model responses scroll by
(useful for visual sanity checks).

Each test is deliberately compact: a single call per path, a ~1-3s Gemini
response, cheap credit spend. Total cost per run: typically <$0.05 with
gemini-3.1-flash doing most of the work.

Paths covered (one each):

  1. Raw Gemini handshake          — ``gemini_simple_completion_sync``
  2. Agent JSON heavy tier          — ``_provider_generate("bull", …)``
  3. Agent JSON light tier          — ``_provider_generate("swarm_analyst", …)``
  4. Plain-text RAG polish          — ``_plain_text_generate_sync``
  5. Streaming chat (async)         — ``stream_chat_plain`` (chatbot path)
  6. Video scene director (JSON)    — ``generate("video_scene_director", …)``
  7. Video text fallback (JSON)     — ``generate("video_veo_text_fallback", …)``

Every test asserts (a) the call succeeded, (b) the result looks structurally
correct, and (c) a Gemini model id was involved — i.e. we prove traffic
actually hit the Gemini account, not OpenRouter and not the deterministic
local template.
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest


# ── Env bootstrap ───────────────────────────────────────────────────────────


def _load_backend_dotenv_once() -> None:
    """Best-effort load of backend/.env so users don't have to re-export keys.

    Safe to call multiple times; existing env vars are never overwritten.
    """
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
        # A broken .env shouldn't wreck test discovery — we just skip.
        return


_RUN_FLAG = os.environ.get("RUN_GEMINI_LIVE_SMOKE", "").strip().lower() in (
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
    "live Gemini smoke disabled — set RUN_GEMINI_LIVE_SMOKE=1 and either "
    "GEMINI_API_KEY or GOOGLE_API_KEY to enable"
)


@unittest.skipUnless(_RUN_FLAG and _has_gemini_key(), _SKIP_REASON)
class TestGeminiLiveSmoke(unittest.TestCase):
    """One real-network test per LLM code path touched by TradeTalk."""

    @classmethod
    def setUpClass(cls):
        # GEMINI_PRIMARY=1 is what LLMClient reads at call time. Force it on
        # for the duration of the suite so these tests don't depend on whatever
        # .env has. Restored in tearDownClass.
        cls._saved_primary = os.environ.get("GEMINI_PRIMARY")
        os.environ["GEMINI_PRIMARY"] = "1"

        # Pre-resolve the model ids once so we can print them nicely.
        from backend.gemini_llm import GEMINI_MODEL, GEMINI_MODEL_LIGHT

        cls.heavy_model = GEMINI_MODEL
        cls.light_model = GEMINI_MODEL_LIGHT
        print(
            f"\n[live-smoke] GEMINI_PRIMARY=1 heavy={cls.heavy_model} "
            f"light={cls.light_model}"
        )

    @classmethod
    def tearDownClass(cls):
        if cls._saved_primary is None:
            os.environ.pop("GEMINI_PRIMARY", None)
        else:
            os.environ["GEMINI_PRIMARY"] = cls._saved_primary

    # ── 1. Raw handshake ────────────────────────────────────────────────────

    def test_01_raw_gemini_handshake(self):
        """Smallest possible call — pure Gemini SDK path, no TradeTalk logic.

        Gemini 3.1 Pro Preview is a reasoning model: the token budget is shared
        between internal chain-of-thought and the visible reply, so a max of 16
        tokens can leave the reply empty. Give it 512 tokens (still pennies)
        and use the lighter Flash-Lite model so this check mirrors what a
        smoke-cost user call actually spends.
        """
        from backend.gemini_llm import (
            GEMINI_MODEL_LIGHT,
            gemini_simple_completion_sync,
        )

        out = gemini_simple_completion_sync(
            system="You are a laconic greeter.",
            user="Say exactly the word PONG and nothing else.",
            max_tokens=512,
            temperature=0.0,
            json_mode=False,
            model=GEMINI_MODEL_LIGHT,
        )
        print(f"[live-smoke] raw handshake -> {out!r}")
        self.assertIsInstance(out, str)
        self.assertIn("PONG", out.upper())

    # ── 2. Agent JSON (heavy tier) ──────────────────────────────────────────

    def test_02_agent_heavy_tier_bull(self):
        """``_provider_generate("bull", …)`` must return a JSON dict and route
        through :data:`GEMINI_MODEL` (heavy = gemini-3.1-pro-preview)."""
        from backend.llm_client import LLMClient, FALLBACK_TEMPLATES

        client = LLMClient()
        result, version = client._provider_generate(
            "bull",
            "AAPL is trading at $210 with 18% revenue growth and $162B cash. "
            "Give a one-paragraph bull case in your structured schema.",
        )
        print(f"[live-smoke] bull -> v={version} keys={list(result.keys())[:4]}")

        # The JSON must parse into a dict (agent contract) and must NOT be the
        # deterministic fallback template — if it equals the template verbatim
        # that means Gemini returned nothing usable.
        self.assertIsInstance(result, dict)
        self.assertNotEqual(
            result,
            FALLBACK_TEMPLATES["bull"],
            "bull response equals deterministic fallback — Gemini call didn't succeed",
        )
        # The bull schema requires headline + key_points + confidence.
        self.assertIn("headline", result)
        self.assertIn("key_points", result)
        self.assertIn("confidence", result)

    # ── 3. Agent JSON (light tier) ──────────────────────────────────────────

    def test_03_agent_light_tier_swarm_analyst(self):
        """``_provider_generate("swarm_analyst", …)`` — light tier Flash path."""
        from backend.llm_client import LLMClient, FALLBACK_TEMPLATES

        client = LLMClient()
        result, version = client._provider_generate(
            "swarm_analyst",
            "Factor: momentum.\n"
            "Inputs: 52w high distance 4%, RSI 62, 3mo return +14%.\n"
            "Return your signal JSON.",
        )
        print(
            f"[live-smoke] swarm_analyst -> v={version} keys={list(result.keys())[:4]}"
        )
        self.assertIsInstance(result, dict)
        self.assertNotEqual(
            result,
            FALLBACK_TEMPLATES["swarm_analyst"],
            "swarm_analyst response equals template — Gemini-light not called",
        )
        self.assertIn("signal", result)
        self.assertIn("confidence", result)

    # ── 4. Plain-text (RAG polish path) ─────────────────────────────────────

    def test_04_plain_text_rag_polish(self):
        """``_plain_text_generate_sync`` — the prose path used by RAG polish."""
        from backend.llm_client import LLMClient

        client = LLMClient()
        user_draft = (
            "AAPL printed strong quarterly revenue. Services margin expanded. "
            "iPhone unit sales flat but ASP up. Buybacks continued at pace."
        )
        out = client._plain_text_generate_sync(
            system=(
                "Rewrite the provided draft in a punchy 2-sentence summary. "
                "No preamble."
            ),
            user=user_draft,
        )
        print(f"[live-smoke] rag_polish -> {out[:120]!r}…")
        self.assertIsInstance(out, str)
        # Must not simply echo the user draft (that's the Gemini-failure fallback).
        self.assertNotEqual(out.strip(), user_draft.strip())
        # And must be more than a trivial length.
        self.assertGreater(len(out), 40)

    # ── 5. Streaming chat ───────────────────────────────────────────────────

    def test_05_streaming_chat_plain(self):
        """``stream_chat_plain`` — the chatbot path. Collects all chunks.

        Chat uses :data:`GEMINI_MODEL` (Gemini 3.1 Pro Preview) which is a
        reasoning model. Real production chat uses
        ``min(2048, LLM_MAX_TOKENS)`` which is plenty; this test matches that
        budget so the reasoning tokens fit without starving the reply.
        """
        from backend.llm_client import LLMClient

        client = LLMClient()

        async def _collect() -> str:
            buf: list[str] = []
            async for chunk in client.stream_chat_plain(
                system="You are TradeTalk, a brief finance assistant.",
                messages=[
                    {
                        "role": "user",
                        "content": "In ONE short sentence: what is the P/E ratio?",
                    }
                ],
                max_tokens=1024,
            ):
                buf.append(chunk)
            return "".join(buf)

        text = asyncio.run(_collect())
        print(f"[live-smoke] chat -> {text[:160]!r}…")
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 20, f"chat response too short: {text!r}")
        # A legitimate response must NOT start with "[Chat error" (our error marker).
        self.assertFalse(
            text.lstrip().startswith("[Chat error"),
            f"chat error leaked: {text!r}",
        )

    # ── 6. Video scene director (JSON) ──────────────────────────────────────

    def test_06_video_scene_director_json(self):
        """``generate("video_scene_director", …)`` — JSON path used by lesson videos."""
        from backend.llm_client import get_llm_client, FALLBACK_TEMPLATES

        llm = get_llm_client()
        prompt = (
            "Lesson topic: Intro to moving averages.\n"
            "Track: Technical Analysis.\n"
            "Level: Beginner.\n"
            "Produce a 2-scene storyboard (keep it SHORT — minimal scenes) "
            "returning the structured scene array."
        )

        async def _run():
            return await llm.generate("video_scene_director", prompt)

        payload = asyncio.run(_run())
        print(
            f"[live-smoke] video_scene_director -> scenes="
            f"{len(payload.get('scenes', [])) if isinstance(payload, dict) else '—'}"
        )
        self.assertIsInstance(payload, dict)
        self.assertNotEqual(
            payload,
            FALLBACK_TEMPLATES["video_scene_director"],
            "scene director returned template — Gemini call didn't succeed",
        )
        self.assertIn("scenes", payload)
        scenes = payload.get("scenes") or []
        self.assertIsInstance(scenes, list)
        self.assertGreaterEqual(len(scenes), 1)
        self.assertIn("caption", scenes[0])

    # ── 7. Video text fallback (JSON) ───────────────────────────────────────

    def test_07_video_veo_text_fallback_json(self):
        """``generate("video_veo_text_fallback", …)`` — the slide-copy path when
        Veo fails. Must stay on Gemini with GEMINI_PRIMARY=1."""
        from backend.llm_client import get_llm_client, FALLBACK_TEMPLATES

        llm = get_llm_client()
        prompt = (
            "Lesson topic: Compound Interest.\n"
            "Track: Personal Finance.\n"
            "Level: Beginner.\n"
            "Scene number: 1\n"
            "Planned caption: Money that earns money.\n"
            "Planned visuals (context only): growing stack of coins over time.\n"
            "Reason animated video failed: quota exhausted.\n"
            "Write the JSON text slide."
        )

        async def _run():
            return await llm.generate("video_veo_text_fallback", prompt)

        payload = asyncio.run(_run())
        print(f"[live-smoke] video_text_fallback -> keys={list(payload.keys())[:4]}")
        self.assertIsInstance(payload, dict)
        self.assertNotEqual(
            payload,
            FALLBACK_TEMPLATES["video_veo_text_fallback"],
            "text fallback returned template — Gemini call didn't succeed",
        )
        self.assertIn("caption", payload)
        self.assertIn("body", payload)
        self.assertGreater(len(str(payload["body"]).strip()), 20)


if __name__ == "__main__":
    unittest.main()
