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
            "POSTGRES_USER",
            "POSTGRES_IAM_AUTH",
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

    def test_iam_auth_enabled_without_password(self):
        with patch.dict(
            os.environ,
            {"PORTFOLIO_STORAGE": "postgres", "POSTGRES_IAM_AUTH": "1"},
            clear=False,
        ):
            self.assertTrue(postgres_enabled())

    def test_iam_tcp_uses_token_password_and_sslmode(self):
        env = {
            "POSTGRES_IAM_AUTH": "1",
            "POSTGRES_USER": "svc@proj.iam",
            "POSTGRES_HOST": "10.0.0.5",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "backend.postgres_config._iam_access_token", return_value="ya29.TOKEN"
        ):
            kw = postgres_connection_kwargs()
            self.assertEqual(kw["password"], "ya29.TOKEN")
            self.assertEqual(kw["user"], "svc@proj.iam")
            self.assertEqual(kw["sslmode"], "require")
            self.assertIn("sslmode=require", postgres_dsn())

    def test_iam_unix_socket_dsn_uses_host_param_no_sslmode(self):
        env = {
            "POSTGRES_IAM_AUTH": "1",
            "POSTGRES_USER": "svc@proj.iam",
            "POSTGRES_HOST": "/cloudsql/proj:us-central1:inst",
            "POSTGRES_DB": "tradetalk",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "backend.postgres_config._iam_access_token", return_value="ya29.TOKEN"
        ):
            kw = postgres_connection_kwargs()
            self.assertNotIn("sslmode", kw)
            dsn = postgres_dsn()
            self.assertTrue(dsn.startswith("postgresql://svc%40proj.iam:ya29.TOKEN@/tradetalk?"))
            self.assertIn("host=%2Fcloudsql%2Fproj%3Aus-central1%3Ainst", dsn)


if __name__ == "__main__":
    unittest.main()
