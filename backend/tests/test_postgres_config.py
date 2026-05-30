"""Offline tests for Postgres portfolio config (no live DB)."""
import os
import unittest
from unittest.mock import patch

from backend import gcp_settings
from backend.postgres_config import (
    postgres_connection_kwargs,
    postgres_dsn,
    postgres_enabled,
)


class TestPostgresConfig(unittest.TestCase):
    def tearDown(self):
        for k in (
            "PORTFOLIO_STORAGE",
            "POSTGRES_PASSWORD",
            "POSTGRES_HOST",
            "DATABASE_URL",
        ):
            os.environ.pop(k, None)

    def test_defaults_from_gcp_settings(self):
        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "secret"}, clear=False):
            kw = postgres_connection_kwargs()
        self.assertEqual(kw["host"], gcp_settings.POSTGRES_HOST)
        self.assertEqual(kw["dbname"], gcp_settings.POSTGRES_DB_NAME)
        self.assertEqual(kw["user"], gcp_settings.POSTGRES_USER)
        self.assertEqual(kw["password"], "secret")

    def test_postgres_enabled_when_storage_postgres_and_password(self):
        with patch.dict(
            os.environ,
            {"PORTFOLIO_STORAGE": "postgres", "POSTGRES_PASSWORD": "x"},
            clear=False,
        ):
            self.assertTrue(postgres_enabled())

    def test_sqlite_when_storage_sqlite(self):
        with patch.dict(
            os.environ,
            {"PORTFOLIO_STORAGE": "sqlite", "POSTGRES_PASSWORD": "x"},
            clear=False,
        ):
            self.assertFalse(postgres_enabled())

    def test_database_url_enables_without_password_env(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://u:p@host:5432/db"},
            clear=False,
        ):
            self.assertTrue(postgres_enabled())
            self.assertEqual(postgres_dsn(), "postgresql://u:p@host:5432/db")


if __name__ == "__main__":
    unittest.main()
