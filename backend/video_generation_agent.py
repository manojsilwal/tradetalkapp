"""
VideoGenerationAgent — uses Google Veo 3.1 to generate animated lesson videos.

Pipeline:
  1. LLM breaks the lesson topic into 8 visual scenes (4 seconds each by default)
  2. Veo (Gemini API / google-genai) generates each scene as a 9:16 720p .mp4
  3. Files saved to static/videos/lesson_{id}/scene_{n:02d}.mp4
  4. Playlist manifest returned to the frontend

Scene JSON is produced via :func:`llm_client.LLMClient.generate` (``video_scene_director``),
which honors ``GEMINI_PRIMARY=1`` to route through Gemini 3.1 Flash end-to-end. MP4 clips
always go through Google Veo (Gemini API). If Veo cannot produce an MP4 for a scene and
``VIDEO_VEO_OPENROUTER_FALLBACK=1`` (default), the same lesson continues with a text slide
from ``video_veo_text_fallback`` — which also honors the Gemini-primary flag, so no
OpenRouter call is made when the flag is on. (The env var keeps its legacy name for
backward compat; think of it as "text-slide fallback is enabled".)

Cost depends on model tier (Lite default is cheaper than full Veo 3.1); see Google AI pricing.
All generated videos are cached — each lesson is generated ONCE, served forever.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .agent_policy_guardrails import redact_secrets_in_text

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "videos")

# Default: Veo 3.1 Lite (Gemini API). Override for full/fast tiers, e.g. veo-3.1-fast-generate-preview
VIDEO_VEO_MODEL = os.environ.get("VIDEO_VEO_MODEL", "veo-3.1-lite-generate-preview").strip()
# When Veo cannot produce an MP4 for a scene, generate a text slide via the
# ``video_veo_text_fallback`` prompt. The LLM used is whatever LLMClient is
# configured for (Gemini 3.1 Flash when GEMINI_PRIMARY=1, OpenRouter otherwise).
VIDEO_VEO_OPENROUTER_FALLBACK = os.environ.get("VIDEO_VEO_OPENROUTER_FALLBACK", "1").strip() != "0"


def _veo_duration_seconds() -> int:
    """Veo only allows 4, 6, or 8 seconds per clip (Gemini API). Default 4."""
    raw = os.environ.get("VIDEO_VEO_DURATION_SECONDS", "4").strip()
    try:
        d = int(raw)
    except ValueError:
        d = 4
    if d not in (4, 6, 8):
        logger.warning(
            "[VideoAgent] VIDEO_VEO_DURATION_SECONDS=%r invalid (allowed 4, 6, 8); using 4",
            raw,
        )
        d = 4
    return d


def _scene_system_prompt(duration: int) -> str:
    return f"""You are a visual director for a high-quality finance education channel.
Your job: break a lesson topic into exactly 8 continuous cinematic scenes for a highly animated explainer video.
Each scene is {duration} seconds. The style must be true moving video showing live examples, moving diagrams, fluid animations, and real-time transitions (NOT a static slideshow).

Rules:
- Each visual_prompt must explicitly describe continuous motion, fluid transitions, moving diagrams, or live animated examples.
- Do NOT describe static slides. Every scene must have dynamic movement.
- Use abstract animated charts, flowing numbers, kinetic typography, and moving motion graphics. No people or real logos.
- Keep captions short (1 sentence max).
- Ensure visual flow feels like a continuous video rather than disjointed slides.

Return ONLY a valid JSON array — no markdown fences, no extra text."""


def _scene_user_template(topic: str, track: str, level: str, duration: int) -> str:
    return f"""Topic: "{topic}"
Track: "{track}"
Level: "{level}"

Return 8 scenes as JSON:
[
  {{
    "scene": 1,
    "visual_prompt": "...",
    "caption": "...",
    "duration": {duration}
  }},
  ...
]"""


async def _generate_scene_script(topic: str, track: str, level: str) -> List[Dict]:
    """Use the shared LLM client (OpenRouter) to create 8 Veo-ready scenes."""
    try:
        from .llm_client import get_llm_client

        d = _veo_duration_seconds()
        llm = get_llm_client()
        user = _scene_user_template(topic, track, level, d)
        payload = await llm.generate("video_scene_director", f"{_scene_system_prompt(d)}\n\n{user}")
        scenes = payload.get("scenes", []) if isinstance(payload, dict) else []
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("LLM did not return scenes payload")
        logger.info(f"[VideoAgent] Generated {len(scenes)} scenes for '{topic}'")
        return scenes
    except Exception as e:
        logger.warning(f"[VideoAgent] Scene script generation failed: {e}")
        return _fallback_scenes(topic)


def _fallback_scenes(topic: str) -> List[Dict]:
    """Deterministic fallback when OpenRouter is unavailable."""
    d = _veo_duration_seconds()
    prompts = [
        f"Animated title card with text '{topic}', bold modern typography, dark gradient background, glowing accent",
        f"2D bar chart animating from zero to full height, finance data visualization style, clean white background",
        f"Floating number counter increasing rapidly, green color, modern sans-serif font, particle effects",
        f"Split screen comparison: two columns labeled BEFORE and AFTER with animated metrics changing",
        f"Animated line chart trending upward with milestone markers, gold and green color scheme",
        f"Motion graphic infographic with three icons appearing sequentially, minimal flat design",
        f"Zoom-in on a formula being written character by character on a digital whiteboard",
        f"Summary card with key takeaways animating in one by one, dark theme with neon accents",
    ]
    captions = [
        f"Today: {topic}",
        "The numbers tell the story",
        "Watch it compound over time",
        "The difference is striking",
        "Consistent growth = wealth",
        "Three things to remember",
        "The formula that matters",
        "Remember these key points",
    ]
    return [{"scene": i+1, "visual_prompt": prompts[i], "caption": captions[i], "duration": d}
            for i in range(8)]


def _resolve_gemini_api_key() -> str:
    """Prefer GEMINI_API_KEY (Google AI Studio) then GOOGLE_API_KEY."""
    k = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    return k


def _extract_operation_error(operation: Any) -> Optional[str]:
    err = getattr(operation, "error", None)
    if not err:
        return None
    if isinstance(err, dict):
        code = err.get("code") or err.get("status")
        msg = err.get("message", "")
        parts = [p for p in (code, msg) if p]
        return ": ".join(str(p) for p in parts) if parts else json.dumps(err)[:500]
    return str(err)[:800]


def _build_genai_client():
    """Import google-genai and construct Client. Raises on missing dependency or invalid key."""
    from google import genai as gai

    api_key = _resolve_gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set — cannot call Veo")
    # SDK also reads GEMINI_API_KEY from the environment; set both for consistency.
    os.environ.setdefault("GEMINI_API_KEY", api_key)
    return gai.Client(api_key=api_key)


async def _text_fallback_playlist_entry(
    scene: Dict,
    topic: str,
    track: str,
    level: str,
    clip_duration: int,
    veo_reason: str,
) -> Dict:
    """
    Text-only slide when Veo MP4 is unavailable. Text generation flows through
    :func:`llm_client.LLMClient.generate` with role ``video_veo_text_fallback``:
    - ``GEMINI_PRIMARY=1`` → Gemini 3.1 Flash (role tier=light) via gemini_llm.
    - otherwise           → OpenRouter, with rule-based ``FALLBACK_TEMPLATES`` as
                            the last resort.
    """
    scene_num = int(scene.get("scene", 0))
    caption_seed = (scene.get("caption") or f"Scene {scene_num}").strip()
    visual = (scene.get("visual_prompt") or "")[:900]

    from .llm_client import get_llm_client

    llm = get_llm_client()
    user = (
        f"Lesson topic: {topic}\nTrack: {track}\nLevel: {level}\n"
        f"Scene number: {scene_num}\n"
        f"Planned caption: {caption_seed}\n"
        f"Planned visuals (context only): {visual}\n"
        f"Reason animated video failed: {veo_reason[:600]}\n"
        "Write the JSON text slide."
    )
    payload = await llm.generate("video_veo_text_fallback", user)
    cap = caption_seed
    body = ""
    if isinstance(payload, dict):
        cap = str(payload.get("caption") or caption_seed).strip() or caption_seed
        body = str(payload.get("body") or "").strip()
    if not body:
        body = (
            f"{caption_seed}. This segment is part of “{topic}” ({track}). "
            "Video rendering was unavailable; use your notes to review the full lesson."
        )

    return {
        "scene": scene_num,
        "url": None,
        "caption": cap,
        "duration": clip_duration,
        "media": "text_fallback",
        "fallback_body": body,
    }


async def generate_lesson_video(
    lesson_id: str,
    topic: str,
    track: str,
    level: str,
    db_update_fn=None,
    error_out: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Full pipeline: script → Veo → save → return playlist manifest.
    db_update_fn(lesson_id, scene_idx, status) is called after each scene completes.
    If generation fails entirely, a short reason is appended to error_out (single-element list).
    """
    def _fail(msg: str) -> List[Dict]:
        logger.error("[VideoAgent] %s", msg)
        if error_out is not None:
            error_out.append(msg)
        return []

    api_key = _resolve_gemini_api_key()
    if not api_key and not VIDEO_VEO_OPENROUTER_FALLBACK:
        return _fail(
            "GEMINI_API_KEY or GOOGLE_API_KEY not set — cannot call Veo (set VIDEO_VEO_OPENROUTER_FALLBACK=1 for text-only)"
        )

    gtypes: Any = None
    client: Any = None
    if api_key:
        try:
            from google.genai import types as gtypes_mod

            gtypes = gtypes_mod
            client = _build_genai_client()
        except ImportError as e:
            if not VIDEO_VEO_OPENROUTER_FALLBACK:
                return _fail(
                    "google-genai package not installed (pip install google-genai). "
                    f"Import error: {e}"
                )
            logger.warning("[VideoAgent] google-genai unavailable — using text fallback: %s", e)
        except Exception as e:
            if not VIDEO_VEO_OPENROUTER_FALLBACK:
                return _fail(f"Veo client init failed: {redact_secrets_in_text(str(e))}")
            logger.warning(
                "[VideoAgent] Veo client init failed — using text fallback: %s",
                redact_secrets_in_text(str(e)),
            )

    scenes = await _generate_scene_script(topic, track, level)
    clip_duration = _veo_duration_seconds()
    os.makedirs(os.path.join(STATIC_DIR, f"lesson_{lesson_id}"), exist_ok=True)

    playlist: List[Dict] = []
    total = len(scenes)
    first_scene_error: Optional[str] = None

    if client is None and VIDEO_VEO_OPENROUTER_FALLBACK:
        reason = "Veo unavailable (no GEMINI_API_KEY / google-genai / client init failed)"
        logger.warning("[VideoAgent] %s — OpenRouter text slides for all scenes", reason)
        for scene in scenes:
            sn = scene["scene"]
            playlist.append(
                await _text_fallback_playlist_entry(
                    scene, topic, track, level, clip_duration, reason,
                )
            )
            if db_update_fn:
                db_update_fn(lesson_id, sn, "fallback")
        return sorted(playlist, key=lambda x: x["scene"])

    assert gtypes is not None and client is not None

    for i, scene in enumerate(scenes):
        scene_num = scene["scene"]
        out_path = os.path.join(STATIC_DIR, f"lesson_{lesson_id}", f"scene_{scene_num:02d}.mp4")

        if os.path.exists(out_path):
            logger.info(f"[VideoAgent] Scene {scene_num} already cached, skipping")
            playlist.append({
                "scene":   scene_num,
                "url":     f"/static/videos/lesson_{lesson_id}/scene_{scene_num:02d}.mp4",
                "caption": scene.get("caption", ""),
                "duration": scene.get("duration", clip_duration),
                "media": "video",
            })
            if db_update_fn:
                db_update_fn(lesson_id, scene_num, "complete")
            continue

        veo_ok = False
        veo_reason = ""

        try:
            logger.info(
                "[VideoAgent] Generating scene %s/%s for lesson %s (model=%s duration=%ss)",
                scene_num,
                total,
                lesson_id,
                VIDEO_VEO_MODEL,
                clip_duration,
            )

            def _start_generation():
                return client.models.generate_videos(
                    model=VIDEO_VEO_MODEL,
                    prompt=scene["visual_prompt"],
                    config=gtypes.GenerateVideosConfig(
                        aspect_ratio="9:16",
                        resolution="720p",
                        number_of_videos=1,
                        duration_seconds=clip_duration,
                    ),
                )

            operation = await asyncio.to_thread(_start_generation)

            max_polls = 60
            polls = 0
            while not operation.done and polls < max_polls:
                await asyncio.sleep(10)
                operation = await asyncio.to_thread(client.operations.get, operation)
                polls += 1
                logger.debug(f"[VideoAgent] Scene {scene_num} poll {polls}: pending")

            if not operation.done:
                veo_reason = f"Scene {scene_num} timed out after {polls * 10}s"
                logger.warning("[VideoAgent] %s", veo_reason)
            else:
                op_err = _extract_operation_error(operation)
                if op_err:
                    veo_reason = f"Veo operation error (scene {scene_num}): {op_err}"
                    logger.error("[VideoAgent] %s", redact_secrets_in_text(veo_reason))
                elif not operation.response or not getattr(operation.response, "generated_videos", None):
                    veo_reason = f"Veo returned no video payload for scene {scene_num}"
                    logger.error("[VideoAgent] %s", veo_reason)
                else:
                    video = operation.response.generated_videos[0]
                    await asyncio.to_thread(client.files.download, file=video.video)
                    await asyncio.to_thread(video.video.save, out_path)
                    logger.info(f"[VideoAgent] Scene {scene_num} saved to {out_path}")
                    playlist.append({
                        "scene":    scene_num,
                        "url":      f"/static/videos/lesson_{lesson_id}/scene_{scene_num:02d}.mp4",
                        "caption":  scene.get("caption", ""),
                        "duration": scene.get("duration", clip_duration),
                        "media":    "video",
                    })
                    veo_ok = True
                    if db_update_fn:
                        db_update_fn(lesson_id, scene_num, "complete")
                    if i < total - 1:
                        await asyncio.sleep(3)

        except Exception as e:
            veo_reason = f"Scene {scene_num} failed: {redact_secrets_in_text(str(e))}"
            logger.error("[VideoAgent] %s", veo_reason)

        if veo_ok:
            continue

        if VIDEO_VEO_OPENROUTER_FALLBACK:
            logger.info("[VideoAgent] Scene %s — OpenRouter text fallback after Veo failure", scene_num)
            playlist.append(
                await _text_fallback_playlist_entry(
                    scene, topic, track, level, clip_duration, veo_reason or "Veo failed",
                )
            )
            if db_update_fn:
                db_update_fn(lesson_id, scene_num, "fallback")
        else:
            if first_scene_error is None:
                first_scene_error = veo_reason or "Veo failed"
            if db_update_fn:
                db_update_fn(lesson_id, scene_num, "failed")

    result = sorted(playlist, key=lambda x: x["scene"])
    if not result and first_scene_error and error_out is not None:
        error_out.append(first_scene_error)
    elif not result and error_out is not None and not error_out:
        error_out.append(
            "No lesson scenes were produced. Enable VIDEO_VEO_OPENROUTER_FALLBACK or fix Veo/Gemini access."
        )
    return result
