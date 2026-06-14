"""Offline tests for GET /health/data-freshness (handler called directly)."""
import unittest
from datetime import date
from unittest import mock

from backend import daily_brief, market_calendar as mc
from backend.routers.health import data_freshness_health


def _store_source(resp):
    return next(s for s in resp["sources"] if s["name"] == "daily_prices_store")


class TestDataFreshnessHealth(unittest.TestCase):
    def test_envelope_shape(self):
        with mock.patch.object(daily_brief, "get_latest_trade_date", lambda: mc.last_completed_session()):
            resp = data_freshness_health()
        for key in ("checked_at", "session_status", "last_completed_session", "any_stale", "sources"):
            self.assertIn(key, resp)
        self.assertIsInstance(resp["sources"], list)
        self.assertTrue(resp["sources"])

    def test_fresh_store_not_stale(self):
        with mock.patch.object(daily_brief, "get_latest_trade_date", lambda: mc.last_completed_session()):
            resp = data_freshness_health()
        src = _store_source(resp)
        self.assertFalse(src["is_stale"])
        self.assertEqual(src["data_class"], "daily_brief")

    def test_stale_store_flagged(self):
        with mock.patch.object(daily_brief, "get_latest_trade_date", lambda: date(2024, 1, 2)):
            resp = data_freshness_health()
        src = _store_source(resp)
        self.assertTrue(src["is_stale"])
        self.assertTrue(resp["any_stale"])


if __name__ == "__main__":
    unittest.main()
