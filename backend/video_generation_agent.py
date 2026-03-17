"""
VideoGenerationAgent — uses Google Veo 3.1 to generate animated lesson videos.

Pipeline:
  1. LLM (OpenRouter Nemotron) breaks the lesson topic into 8 visual scenes (8 seconds each)
  2. Veo generates each scene as a 9:16 720p .mp4
  3. Files saved to static/videos/lesson_{id}/scene_{n:02d}.mp4
  4. Playlist manifest returned to the frontend

Cost: ~$1.20/scene at Veo 720p Fast tier → ~$9.60 for a 1-min lesson (8 scenes)
All generated videos are cached — each lesson is generated ONCE, served forever.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "videos")

SCENE_SYSTEM_PROMPT = """You are a visual director for a TikTok-style finance education channel.
Your job: break a lesson topic into exactly 8 cinematic scenes for an animated explainer video.
Each scene is 8 seconds. The style is modern 2D motion graphics — clean, colourful, fast-paced.

Rules:
- Each visual_prompt must be self-contained and describe ONLY what to animate visually
- No people, no real company logos — use abstract charts, numbers, icons, motion graphics
- Keep captions short (1 sentence max)
- Vary the visual style between scenes for variety

Return ONLY a valid JSON array — no markdown fences, no extra text."""

SCENE_USER_TEMPLATE = """Topic: "{topic}"
Track: "{track}"
Level: "{level}"

Return 8 scenes as JSON:
[
  {{
    "scene": 1,
    "visual_prompt": "...",
    "caption": "...",
    "duration": 8
  }},
  ...
]"""


async def _generate_scene_script(topic: str, track: str, level: str) -> List[Dict]:
    """Use OpenRouter Nemotron to break the lesson into 8 Veo-ready scenes."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        logger.warning("[VideoAgent] No OPENROUTER_API_KEY — using fallback scenes")
        return _fallback_scenes(topic)

    try:
        from openai import OpenAI

        headers = {}
        openrouter_referer = os.environ.get("OPENROUTER_HTTP_REFERER", "")
        openrouter_title = os.environ.get("OPENROUTER_X_TITLE", "TradeTalk App")
        if openrouter_referer:
            headers["HTTP-Referer"] = openrouter_referer
        if openrouter_title:
            headers["X-Title"] = openrouter_title

        client = OpenAI(
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=openrouter_key,
            default_headers=headers,
        )
        prompt = SCENE_USER_TEMPLATE.format(topic=topic, track=track, level=level)
        completion = client.chat.completions.create(
            model=os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"),
            messages=[
                {"role": "system", "content": SCENE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw:
            raise ValueError("OpenRouter returned empty scene script")
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        scenes = json.loads(raw)
        logger.info(f"[VideoAgent] Generated {len(scenes)} scenes for '{topic}'")
        return scenes
    except Exception as e:
        logger.warning(f"[VideoAgent] Scene script generation failed: {e}")
        return _fallback_scenes(topic)


def _fallback_scenes(topic: str) -> List[Dict]:
    """Deterministic fallback when OpenRouter is unavailable."""
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
    return [{"scene": i+1, "visual_prompt": prompts[i], "caption": captions[i], "duration": 8}
            for i in range(8)]


async def generate_lesson_video(lesson_id: str, topic: str, track: str,
                                 level: str, db_update_fn=None) -> List[Dict]:
    """
    Full pipeline: script → Veo → save → return playlist manifest.
    db_update_fn(lesson_id, scene_idx, status) is called after each scene completes.
    """
    google_api_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not google_api_key:
        logger.error("[VideoAgent] GOOGLE_API_KEY not set — cannot call Veo")
        return []

    scenes = await _generate_scene_script(topic, track, level)
    os.makedirs(os.path.join(STATIC_DIR, f"lesson_{lesson_id}"), exist_ok=True)

    try:
        from google import genai as gai
        from google.genai import types as gtypes
        client = gai.Client(api_key=google_api_key)
    except ImportError:
        logger.error("[VideoAgent] google-generativeai not installed")
        return []

    playlist: List[Dict] = []
    total = len(scenes)

    for i, scene in enumerate(scenes):
        scene_num = scene["scene"]
        out_path  = os.path.join(STATIC_DIR, f"lesson_{lesson_id}", f"scene_{scene_num:02d}.mp4")

        # Skip if already generated
        if os.path.exists(out_path):
            logger.info(f"[VideoAgent] Scene {scene_num} already cached, skipping")
            playlist.append({
                "scene":   scene_num,
                "url":     f"/static/videos/lesson_{lesson_id}/scene_{scene_num:02d}.mp4",
                "caption": scene.get("caption", ""),
                "duration": scene.get("duration", 8),
            })
            if db_update_fn:
                db_update_fn(lesson_id, scene_num, "complete")
            continue

        try:
            logger.info(f"[VideoAgent] Generating scene {scene_num}/{total} for lesson {lesson_id}")
            operation = client.models.generate_videos(
                model="veo-3.1-generate-preview",
                prompt=scene["visual_prompt"],
                config=gtypes.GenerateVideosConfig(
                    aspect_ratio="9:16",
                    resolution="720p",
                    number_of_videos=1,
                ),
            )

            # Poll until complete (Veo is a long-running operation)
            max_polls = 60   # max 10 minutes
            polls     = 0
            while not operation.done and polls < max_polls:
                await asyncio.sleep(10)
                operation = client.operations.get(operation)
                polls += 1
                logger.debug(f"[VideoAgent] Scene {scene_num} poll {polls}: pending")

            if not operation.done:
                logger.warning(f"[VideoAgent] Scene {scene_num} timed out after {polls * 10}s")
                continue

            video = operation.response.generated_videos[0]
            client.files.download(file=video.video)
            video.video.save(out_path)
            logger.info(f"[VideoAgent] Scene {scene_num} saved to {out_path}")

            playlist.append({
                "scene":    scene_num,
                "url":      f"/static/videos/lesson_{lesson_id}/scene_{scene_num:02d}.mp4",
                "caption":  scene.get("caption", ""),
                "duration": scene.get("duration", 8),
            })
            if db_update_fn:
                db_update_fn(lesson_id, scene_num, "complete")

            # Stagger submissions to stay within Veo rate limits
            if i < total - 1:
                await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"[VideoAgent] Scene {scene_num} failed: {e}")
            if db_update_fn:
                db_update_fn(lesson_id, scene_num, "failed")

    return sorted(playlist, key=lambda x: x["scene"])
