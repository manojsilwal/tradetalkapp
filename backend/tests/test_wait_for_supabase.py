"""Tests for scripts/wait_for_supabase.py (no network)."""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# Load script as module (kebab-free name not on PYTHONPATH as package).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wait_for_supabase.py"
_spec = importlib.util.spec_from_file_location("wait_for_supabase_cli", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["wait_for_supabase_cli"] = _mod
_spec.loader.exec_module(_mod)


class TestWaitForSupabase(unittest.TestCase):
    def test_int_env_empty_string_uses_default(self) -> None:
        self.assertEqual(_mod._int_env("NONEXISTENT___XYZ", 42), 42)

    def test_exits_zero_when_rest_returns_200(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://abc.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "test-key",
                "WAIT_MAX_SECONDS": "60",
                "WAIT_INTERVAL_SECONDS": "1",
            },
            clear=False,
        ):
            r = mock.Mock()
            r.status_code = 200
            with mock.patch("wait_for_supabase_cli.requests.get", return_value=r):
                self.assertEqual(_mod.main(), 0)

    def test_exits_one_on_401(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://abc.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "bad",
                "WAIT_MAX_SECONDS": "60",
                "WAIT_INTERVAL_SECONDS": "1",
            },
            clear=False,
        ):
            r = mock.Mock()
            r.status_code = 401
            with mock.patch("wait_for_supabase_cli.requests.get", return_value=r):
                self.assertEqual(_mod.main(), 1)


if __name__ == "__main__":
    unittest.main()
