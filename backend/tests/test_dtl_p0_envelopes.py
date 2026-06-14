"""Offline tests for Data Trust Layer P0 envelopes (macro + stock-fundamentals)."""
import unittest
from datetime import date

from backend import freshness as fr
from backend import market_calendar as mc
from backend.schemas import DataFreshness, FreshnessTier


class TestAssessSpot(unittest.TestCase):
    def test_spot_just_fetched_is_fresh(self):
        f = fr.assess_spot("yfinance_vix")
        self.assertFalse(f.is_stale)
        self.assertIn(f.tier, (FreshnessTier.LIVE, FreshnessTier.DELAYED))
        self.assertIsNotNone(f.captured_at)

    def test_spot_degraded_passthrough(self):
        f = fr.assess_spot("stooq", degraded=True)
        self.assertTrue(f.degraded)


class TestStockFundamentalsHelper(unittest.TestCase):
    def test_latest_price_date_picks_max(self):
        from backend.routers.analysis import _latest_price_date

        result = {
            "price_history": {
                "1mo": [{"timestamp": "2026-06-10T00:00:00"}, {"timestamp": "2026-06-12T00:00:00"}],
                "6mo": [{"timestamp": "2026-06-11T00:00:00"}],
            }
        }
        self.assertEqual(_latest_price_date(result), "2026-06-12")

    def test_latest_price_date_none_when_empty(self):
        from backend.routers.analysis import _latest_price_date

        self.assertIsNone(_latest_price_date({"price_history": {"1mo": []}}))
        self.assertIsNone(_latest_price_date({}))

    def test_stale_upstream_flagged_via_session_policy(self):
        # A two-year-old latest bar must be flagged stale.
        f = fr.assess(data_class="session_pct", source="yfinance", as_of=date(2024, 1, 2))
        self.assertTrue(f.is_stale)
        # Current session bar is fresh.
        f2 = fr.assess(data_class="session_pct", source="yfinance", as_of=mc.last_completed_session())
        self.assertFalse(f2.is_stale)


class TestMacroHelper(unittest.TestCase):
    def test_macro_freshness_returns_envelope(self):
        from backend.routers.macro import _macro_data_freshness

        f = _macro_data_freshness()
        self.assertTrue(f is None or isinstance(f, DataFreshness))
        if f is not None:
            self.assertFalse(f.is_stale)


if __name__ == "__main__":
    unittest.main()
