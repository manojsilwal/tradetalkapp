#!/usr/bin/env python3
"""
Single Veo generate_videos smoke test (Gemini API).

Note: Veo does not support 1-second clips. Allowed durations are 4, 6, or 8 seconds
(see Gemini API video docs). This script defaults to 4 seconds (shortest).

Requires GEMINI_API_KEY or GOOGLE_API_KEY in the environment. Optionally loads
backend/.env if present (simple KEY=VAL lines).

Usage (from repo root):
  PYTHONPATH=backend python backend/scripts/veo_smoke_test.py
  PYTHONPATH=backend python backend/scripts/veo_smoke_test.py --duration 4
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def _load_dotenv_simple(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def main() -> int:
    repo_backend = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    _load_dotenv_simple(os.path.join(repo_backend, ".env"))

    parser = argparse.ArgumentParser(description="Veo one-shot smoke test")
    parser.add_argument(
        "--duration",
        type=int,
        default=4,
        choices=(4, 6, 8),
        help="Clip length in seconds (Veo minimum is 4; 1s is not supported)",
    )
    args = parser.parse_args()

    key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        print("Set GEMINI_API_KEY or GOOGLE_API_KEY", file=sys.stderr)
        return 1
    os.environ.setdefault("GEMINI_API_KEY", key)

    model = os.environ.get("VIDEO_VEO_MODEL", "veo-3.1-lite-generate-preview").strip()

    from google import genai
    from google.genai import types as gtypes

    out_dir = os.path.join(repo_backend, "static", "videos", "smoke_test")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"smoke_{args.duration}s.mp4")

    client = genai.Client(api_key=key)
    prompt = (
        "Abstract animated green upward trending line chart on dark background, "
        "minimal 2D motion graphics, finance style, no people, no logos, no text."
    )

    print(f"model={model} duration={args.duration}s resolution=720p aspect=9:16")
    print(f"output={out_path}")

    operation = client.models.generate_videos(
        model=model,
        prompt=prompt,
        config=gtypes.GenerateVideosConfig(
            aspect_ratio="9:16",
            resolution="720p",
            number_of_videos=1,
            duration_seconds=args.duration,
        ),
    )

    max_polls = 120
    polls = 0
    while not operation.done and polls < max_polls:
        time.sleep(10)
        operation = client.operations.get(operation)
        polls += 1
        print(f"poll {polls} done={operation.done}")

    if not operation.done:
        print("Timed out waiting for Veo operation", file=sys.stderr)
        return 1

    err = getattr(operation, "error", None)
    if err:
        print("Operation error:", err, file=sys.stderr)
        return 1

    if not operation.response or not getattr(operation.response, "generated_videos", None):
        print("No generated_videos in response", file=sys.stderr)
        return 1

    video = operation.response.generated_videos[0]
    client.files.download(file=video.video)
    video.video.save(out_path)
    sz = os.path.getsize(out_path)
    print(f"OK wrote {sz} bytes -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
