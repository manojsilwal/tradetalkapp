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

    def test_applies_decisions_migration(self):
        """Decision-Outcome Ledger schema (Phase 2 of the moat) seeds all 5 tables."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            applied = run_migrations(db_path, "decisions")
            self.assertIn("001_initial.sql", applied)

            conn = sqlite3.connect(db_path)
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            for t in (
                "decision_events",
                "decision_evidence",
                "outcome_observations",
                "feature_snapshots",
                "contract_violations",
                "_schema_migrations",
            ):
                self.assertIn(t, tables, f"missing table {t}")

            # Critical indexes exist (spot-check the correlation-query hot paths).
            indexes = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()}
            for idx in (
                "idx_decision_events_created_at",
                "idx_decision_events_type_sym",
                "idx_outcome_obs_decision",
                "uq_outcome_obs_unique",
                "idx_feature_snap_name",
                "idx_contract_viol_model",
            ):
                self.assertIn(idx, indexes, f"missing index {idx}")

            # The unique constraint prevents duplicate grades for the same
            # (decision, horizon, metric). This is what keeps the grader
            # idempotent when rerun.
            now = 0.0
            conn.execute(
                "INSERT INTO decision_events "
                "(decision_id, created_at, decision_type, verdict) "
                "VALUES (?, ?, ?, ?)",
                ("d1", now, "swarm", "BUY"),
            )
            conn.execute(
                "INSERT INTO outcome_observations "
                "(decision_id, horizon, as_of_ts, metric, value, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("d1", "1d", now, "price_return_pct", 0.5, now),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO outcome_observations "
                    "(decision_id, horizon, as_of_ts, metric, value, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("d1", "1d", now, "price_return_pct", 1.5, now),
                )
            conn.close()
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
