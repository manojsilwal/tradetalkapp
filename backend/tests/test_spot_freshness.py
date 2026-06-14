"""Offline tests for the canonical provenance-stamped spot fetch (Workstream C)."""
import unittest
from unittest import mock

from backend.connectors import spot
from backend.data_errors import InsufficientDataError
from backend.schemas import FreshnessTier


class TestGetSpotWithFreshness(unittest.TestCase):
    def test_live_provider_not_degraded(self):
        with mock.patch("backend.connectors.quote_fallbacks.fetch_us_equity_spot", return_value=(123.45, "yahoo_chart")):
            price, fresh = spot.get_spot_with_freshness("AAPL")
        self.assertEqual(price, 123.45)
        self.assertFalse(fresh.is_stale)
        self.assertFalse(fresh.degraded)
        self.assertIn(fresh.tier, (FreshnessTier.LIVE, FreshnessTier.DELAYED))

    def test_fallback_provider_marked_degraded(self):
        with mock.patch("backend.connectors.quote_fallbacks.fetch_us_equity_spot", return_value=(50.0, "stooq")):
            price, fresh = spot.get_spot_with_freshness("AAPL")
        self.assertEqual(price, 50.0)
        self.assertTrue(fresh.degraded)

    def test_no_quote_non_strict_returns_stale_envelope(self):
        with mock.patch("backend.connectors.quote_fallbacks.fetch_us_equity_spot", return_value=None):
            price, fresh = spot.get_spot_with_freshness("ZZZZ")
        self.assertIsNone(price)
        self.assertTrue(fresh.is_stale)

    def test_strict_raises_when_market_open(self):
        with mock.patch("backend.connectors.quote_fallbacks.fetch_us_equity_spot", return_value=None), \
                mock.patch("backend.market_calendar.is_market_open", return_value=True):
            with self.assertRaises(InsufficientDataError):
                spot.get_spot_with_freshness("ZZZZ", strict_when_open=True)

    def test_strict_silent_when_market_closed(self):
        with mock.patch("backend.connectors.quote_fallbacks.fetch_us_equity_spot", return_value=None), \
                mock.patch("backend.market_calendar.is_market_open", return_value=False):
            price, fresh = spot.get_spot_with_freshness("ZZZZ", strict_when_open=True)
        self.assertIsNone(price)
        self.assertTrue(fresh.is_stale)


if __name__ == "__main__":
    unittest.main()
