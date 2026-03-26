"""Tests for the migration runner."""
import os
import tempfile
import unittest
import sqlite3

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.migrations.runner import run_migrations


class TestMigrationRunner(unittest.TestCase):
    """Migration runner behavior."""

    def test_applies_initial_progress_migration(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            applied = run_migrations(db_path, "progress")
            self.assertIn("001_initial_schema.sql", applied)

            conn = sqlite3.connect(db_path)
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            self.assertIn("users", tables)
            self.assertIn("user_progress", tables)
            self.assertIn("xp_history", tables)
            self.assertIn("_schema_migrations", tables)
            conn.close()
        finally:
            os.unlink(db_path)

    def test_idempotent_rerun(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            first = run_migrations(db_path, "progress")
            second = run_migrations(db_path, "progress")
            self.assertTrue(len(first) > 0)
            self.assertEqual(len(second), 0)
        finally:
            os.unlink(db_path)

    def test_nonexistent_group_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            applied = run_migrations(db_path, "nonexistent_group")
            self.assertEqual(applied, [])
        finally:
            os.unlink(db_path)

    def test_applies_alerts_migration(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            applied = run_migrations(db_path, "alerts")
            self.assertIn("001_initial_schema.sql", applied)

            conn = sqlite3.connect(db_path)
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            self.assertIn("alerts", tables)
            conn.close()
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
