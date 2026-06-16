"""yfinance capability circuit breaker — offline tests."""
import os
import time
import unittest
from unittest.mock import patch

from backend.connectors.yfinance_capability import (
    record_failure,
    record_success,
    reset_all_for_tests,
    should_attempt,
    status_snapshot,
)


class TestYfinanceCapability(unittest.TestCase):
    def setUp(self):
        reset_all_for_tests()

    def tearDown(self):
        reset_all_for_tests()

    def test_force_disabled_category(self):
        with patch.dict(os.environ, {"YF_DISABLED_CATEGORIES": "info;news"}, clear=False):
            reset_all_for_tests()
            self.assertFalse(should_attempt("info"))
            self.assertFalse(should_attempt("news"))
            self.assertTrue(should_attempt("price"))

    def test_opens_after_threshold_failures(self):
        for _ in range(3):
            record_failure("price")
        self.assertFalse(should_attempt("price"))

    def test_success_resets_breaker(self):
        for _ in range(3):
            record_failure("price")
        record_success("price")
        self.assertTrue(should_attempt("price"))

    def test_half_open_after_cooldown(self):
        from backend.connectors import yfinance_capability as yc

        for _ in range(3):
            record_failure("chart")
        st = yc._state("chart")
        with yc._lock:
            st.opened_at = time.time() - 1000
        self.assertTrue(should_attempt("chart"))

    def test_status_snapshot(self):
        record_failure("info")
        snap = status_snapshot()
        self.assertIn("info", snap)
        self.assertEqual(snap["info"]["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
