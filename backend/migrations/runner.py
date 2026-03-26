"""
Lightweight SQL migration runner for SQLite databases.

Stores applied migrations in a `_schema_migrations` table within each database.
Migrations are plain .sql files in numbered subdirectories, executed in order.

Usage at startup:
    from backend.migrations.runner import run_migrations
    run_migrations("progress.db", "progress")
    run_migrations("alerts.db", "alerts")
"""
import os
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


def run_migrations(db_path: str, migration_group: str) -> list[str]:
    """
    Apply pending migrations for a given database.

    Args:
        db_path: Absolute path to the SQLite database file.
        migration_group: Subdirectory name under migrations/ (e.g. "progress", "alerts").

    Returns:
        List of migration filenames that were applied.
    """
    group_dir = MIGRATIONS_DIR / migration_group
    if not group_dir.is_dir():
        logger.debug("[Migrations] No migration directory for group '%s'", migration_group)
        return []

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at REAL NOT NULL
        )
    """)
    conn.commit()

    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM _schema_migrations").fetchall()
    }

    sql_files = sorted(
        f for f in group_dir.iterdir()
        if f.suffix == ".sql" and f.name not in applied
    )

    newly_applied = []
    for sql_file in sql_files:
        version = sql_file.name
        logger.info("[Migrations] Applying %s/%s", migration_group, version)
        sql = sql_file.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            import time
            conn.execute(
                "INSERT INTO _schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, time.time()),
            )
            conn.commit()
            newly_applied.append(version)
        except Exception as e:
            logger.error("[Migrations] Failed to apply %s/%s: %s", migration_group, version, e)
            raise

    conn.close()

    if newly_applied:
        logger.info(
            "[Migrations] Applied %d migration(s) to %s: %s",
            len(newly_applied), migration_group, newly_applied,
        )
    return newly_applied
