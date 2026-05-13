"""Macro flow SQLite path and migration bootstrap."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..migrations.runner import run_migrations

logger = logging.getLogger(__name__)


def get_macro_flow_db_path() -> str:
    """Path to macro_flow SQLite (env MACRO_FLOW_DB_PATH or backend/macro_flow.db)."""
    env = os.environ.get("MACRO_FLOW_DB_PATH", "").strip()
    if env:
        return env
    root = Path(__file__).resolve().parents[1]
    return str(root / "macro_flow.db")


def init_macro_flow_db() -> None:
    """Apply macro_flow migrations if the DB file is writable."""
    path = get_macro_flow_db_path()
    try:
        run_migrations(path, "macro_flow")
        logger.info("[macro_flow] migrations applied for %s", path)
    except OSError as e:
        logger.warning("[macro_flow] could not run migrations on %s: %s", path, e)
