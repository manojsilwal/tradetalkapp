"""
Load backend/.env (and optional .env.local) before other backend modules read os.environ.

Uvicorn imports backend.main → deps → knowledge_store / llm_client; deps imports this first.

Empty values in ``.env.local`` must not wipe real keys from ``.env`` (e.g. ``GEMINI_API_KEY=``).
"""
from __future__ import annotations

import os
from pathlib import Path

# Keys that must not be blanked by an empty override in .env.local
_PROTECTED_API_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_KEY_2",
    "NVIDIA_API_KEY",
    "YOUTUBE_API_KEY",
)


def _load() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    preserved = {
        k: os.environ[k]
        for k in _PROTECTED_API_KEYS
        if os.environ.get(k, "").strip()
    }
    load_dotenv(root / ".env.local", override=True)
    for key, value in preserved.items():
        if not os.environ.get(key, "").strip():
            os.environ[key] = value
    if "OPENROUTER_KEY" in os.environ and os.environ["OPENROUTER_KEY"].strip():
        os.environ["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_KEY"]


_load()
