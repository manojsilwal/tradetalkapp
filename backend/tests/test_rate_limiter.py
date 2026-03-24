"""Tests for the rate limiter."""
import os
import unittest

os.environ["RATE_LIMIT_ENABLED"] = "1"

from backend.rate_limiter import _check, _hits, _lock, LIMITS


class TestRateLimiter(unittest.TestCase):
    """Rate limiter sliding window tests."""

    def setUp(self):
        with _lock:
            _hits.clear()

    def test_allows_under_limit(self):
        for _ in range(5):
            self.assertTrue(_check("1.2.3.4", "expensive"))

    def test_blocks_over_limit(self):
        max_req = LIMITS["expensive"][0]
        for _ in range(max_req):
            self.assertTrue(_check("5.6.7.8", "expensive"))
        self.assertFalse(_check("5.6.7.8", "expensive"))

    def test_different_ips_independent(self):
        max_req = LIMITS["expensive"][0]
        for _ in range(max_req):
            _check("10.0.0.1", "expensive")
        self.assertFalse(_check("10.0.0.1", "expensive"))
        self.assertTrue(_check("10.0.0.2", "expensive"))

    def test_different_groups_independent(self):
        max_req = LIMITS["expensive"][0]
        for _ in range(max_req):
            _check("20.0.0.1", "expensive")
        self.assertFalse(_check("20.0.0.1", "expensive"))
        self.assertTrue(_check("20.0.0.1", "default"))

    def test_disabled_always_allows(self):
        import backend.rate_limiter as rl
        original = rl.RATE_LIMIT_ENABLED
        try:
            rl.RATE_LIMIT_ENABLED = False
            for _ in range(100):
                self.assertTrue(_check("30.0.0.1", "expensive"))
        finally:
            rl.RATE_LIMIT_ENABLED = original


if __name__ == "__main__":
    unittest.main()
