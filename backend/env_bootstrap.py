"""
Load backend/.env (and optional .env.local) before other backend modules read os.environ.

Uvicorn imports backend.main → deps → knowledge_store / llm_client; deps imports this first.
"""
from __future__ import annotations

from pathlib import Path


def _load() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    load_dotenv(root / ".env.local", override=True)


_load()
