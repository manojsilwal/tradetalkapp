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


def _resp(status: int, *, json_body: dict | None = None):
    m = mock.Mock()
    m.status_code = status
    if json_body is not None:
        m.json.return_value = json_body
    return m


class TestWaitForSupabase(unittest.TestCase):
    def test_int_env_empty_string_uses_default(self) -> None:
        self.assertEqual(_mod._int_env("NONEXISTENT___XYZ", 42), 42)

    def test_exits_zero_when_rest_and_vector_memory_ok(self) -> None:
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
            with mock.patch(
                "wait_for_supabase_cli.requests.get",
                side_effect=[_resp(200), _resp(200)],
            ):
                self.assertEqual(_mod.main(), 0)

    def test_exits_one_when_vector_memory_missing(self) -> None:
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
            with mock.patch(
                "wait_for_supabase_cli.requests.get",
                side_effect=[
                    _resp(200),
                    _resp(
                        404,
                        json_body={
                            "code": "PGRST205",
                            "message": "Could not find the table 'public.vector_memory' in the schema cache",
                        },
                    ),
                ],
            ):
                self.assertEqual(_mod.main(), 1)

    def test_skip_vector_memory_check_skips_second_probe(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://abc.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "test-key",
                "WAIT_MAX_SECONDS": "60",
                "WAIT_INTERVAL_SECONDS": "1",
                "SKIP_VECTOR_MEMORY_CHECK": "1",
            },
            clear=False,
        ):
            m_get = mock.Mock(return_value=_resp(200))
            with mock.patch("wait_for_supabase_cli.requests.get", m_get):
                self.assertEqual(_mod.main(), 0)
            self.assertEqual(m_get.call_count, 1)

    def test_exits_one_on_401_during_wake(self) -> None:
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
            with mock.patch("wait_for_supabase_cli.requests.get", return_value=_resp(401)):
                self.assertEqual(_mod.main(), 1)


if __name__ == "__main__":
    unittest.main()
