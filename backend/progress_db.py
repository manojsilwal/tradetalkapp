"""Canonical SQLite path for progress.db (portfolio, auth, XP, preferences, …)."""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_PATH = os.path.join(_BACKEND_DIR, "progress.db")


def resolve_progress_db_path() -> str:
    """
    Resolve progress.db location.

    - ``PROGRESS_DB_PATH`` — explicit file path (use on GCP: ``/app/data/progress.db``)
    - ``TRADETALK_DATA_DIR`` — directory; file is ``<dir>/progress.db``
    - default — ``backend/progress.db`` next to this package (local dev)
    """
    explicit = os.environ.get("PROGRESS_DB_PATH", "").strip()
    if explicit:
        parent = os.path.dirname(explicit)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return explicit
    data_dir = os.environ.get("TRADETALK_DATA_DIR", "").strip()
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "progress.db")
    return _LEGACY_PATH


def migrate_legacy_progress_db_if_needed() -> None:
    """One-time copy from ephemeral container path to PROGRESS_DB_PATH volume."""
    target = resolve_progress_db_path()
    if target == _LEGACY_PATH or not os.path.isfile(_LEGACY_PATH):
        return
    if os.path.isfile(target):
        return
    try:
        shutil.copy2(_LEGACY_PATH, target)
        logger.info("[progress_db] migrated legacy DB to %s", target)
    except OSError as e:
        logger.warning("[progress_db] legacy migration skipped: %s", e)
