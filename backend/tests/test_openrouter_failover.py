"""OpenRouter pool failover helpers."""
import unittest
from unittest.mock import patch

from backend.openrouter_pool import is_openrouter_rate_limit_error, sync_failover_execute


class TestRateLimitDetection(unittest.TestCase):
    def test_status_429(self):
        e = type("E", (), {"status_code": 429})()
        self.assertTrue(is_openrouter_rate_limit_error(e))

    def test_message_429(self):
        self.assertTrue(is_openrouter_rate_limit_error(RuntimeError("Error code: 429 - provider")))

    def test_not_rate_limit(self):
        self.assertFalse(is_openrouter_rate_limit_error(ValueError("invalid json")))


class TestSyncFailoverExecute(unittest.TestCase):
    @patch("backend.openrouter_pool.time.sleep", lambda _s: None)
    def test_retry_same_key_then_success(self):
        n = {"c": 0}

        def fn(_client):
            n["c"] += 1
            if n["c"] == 1:
                raise RuntimeError("Error code: 429")
            return "ok"

        r, e = sync_failover_execute(["k"], fn)
        self.assertEqual(r, "ok")
        self.assertIsNone(e)

    @patch("backend.openrouter_pool.time.sleep", lambda _s: None)
    def test_failover_to_second_client(self):
        def fn(client):
            if client == "a":
                raise RuntimeError("429 rate limited")
            return "second"

        r, e = sync_failover_execute(["a", "b"], fn)
        self.assertEqual(r, "second")
        self.assertIsNone(e)

    def test_non_429_returns_immediately(self):
        def fn(_client):
            raise ValueError("bad request")

        r, e = sync_failover_execute(["k"], fn)
        self.assertIsNone(r)
        self.assertIsInstance(e, ValueError)

    @patch("backend.openrouter_pool.time.sleep", lambda _s: None)
    def test_exit_immediately_on_rate_limit_skips_second_key(self):
        calls = []

        def fn(client):
            calls.append(client)
            raise RuntimeError("429 rate limit")

        r, e = sync_failover_execute(
            ["a", "b"],
            fn,
            exit_immediately_on_rate_limit=True,
        )
        self.assertIsNone(r)
        self.assertTrue(calls == ["a"])


if __name__ == "__main__":
    unittest.main()
