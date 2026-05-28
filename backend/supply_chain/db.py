"""Supply chain SQLite path and migration bootstrap."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..migrations.runner import run_migrations

logger = logging.getLogger(__name__)


def get_supply_chain_db_path() -> str:
    env = os.environ.get("SUPPLY_CHAIN_DB_PATH", "").strip()
    if env:
        return env
    root = Path(__file__).resolve().parents[1]
    return str(root / "supply_chain.db")


def init_supply_chain_db() -> None:
    path = get_supply_chain_db_path()
    try:
        run_migrations(path, "supply_chain")
        logger.info("[supply_chain] migrations applied for %s", path)
    except OSError as e:
        logger.warning("[supply_chain] could not run migrations on %s: %s", path, e)
