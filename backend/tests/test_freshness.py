"""Offline tests for the Data Trust Layer freshness policy registry (backend/freshness.py)."""
import unittest
from datetime import date, datetime, timedelta, timezone

from backend import freshness as fr
from backend import market_calendar as mc
from backend.schemas import FreshnessTier


class TestAgePolicies(unittest.TestCase):
    def test_live_quote_fresh(self):
        now = datetime.now(timezone.utc)
        f = fr.assess(data_class="live_quote", source="yfinance_live",
                      captured_at=now - timedelta(seconds=5), now=now)
        self.assertFalse(f.is_stale)
        self.assertEqual(f.tier, FreshnessTier.LIVE)
        self.assertLess(f.staleness_seconds, 60)

    def test_live_quote_stale_when_old(self):
        now = datetime.now(timezone.utc)
        f = fr.assess(data_class="live_quote", source="yfinance_live",
                      captured_at=now - timedelta(seconds=120), now=now)
        self.assertTrue(f.is_stale)

    def test_age_policy_missing_timestamp_is_stale(self):
        f = fr.assess(data_class="live_quote", source="yfinance_live")
        self.assertTrue(f.is_stale)

    def test_macro_fred_age(self):
        now = datetime.now(timezone.utc)
        fresh = fr.assess(data_class="macro_fred", source="fred",
                          captured_at=now - timedelta(hours=1), now=now)
        stale = fr.assess(data_class="macro_fred", source="fred",
                          captured_at=now - timedelta(hours=40), now=now)
        self.assertFalse(fresh.is_stale)
        self.assertTrue(stale.is_stale)


class TestSessionPolicies(unittest.TestCase):
    def test_eod_movers_current_session_fresh(self):
        f = fr.assess(data_class="eod_movers", source="snapshot",
                      as_of=mc.last_completed_session())
        self.assertFalse(f.is_stale)
        self.assertEqual(f.tier, FreshnessTier.EOD)
        self.assertIsNotNone(f.expected_as_of)

    def test_daily_brief_two_year_old_is_stale(self):
        f = fr.assess(data_class="daily_brief", source="snapshot", as_of=date(2024, 1, 2))
        self.assertTrue(f.is_stale)
        self.assertGreater(f.staleness_seconds, 800 * 86400)

    def test_session_policy_missing_as_of_is_stale(self):
        f = fr.assess(data_class="session_pct", source="yfinance_eod")
        self.assertTrue(f.is_stale)


class TestSpecialPolicies(unittest.TestCase):
    def test_reference_never_stale(self):
        f = fr.assess(data_class="reference", source="static_table", as_of=date(2000, 1, 1))
        self.assertFalse(f.is_stale)
        self.assertEqual(f.tier, FreshnessTier.REFERENCE)

    def test_unknown_class_falls_back_without_crashing(self):
        f = fr.assess(data_class="totally_made_up", source="x", as_of=mc.last_completed_session())
        self.assertFalse(f.is_stale)  # current session under default session policy

    def test_degraded_flag_passthrough(self):
        now = datetime.now(timezone.utc)
        f = fr.assess(data_class="live_quote", source="stooq",
                      captured_at=now, degraded=True, now=now)
        self.assertTrue(f.degraded)

    def test_spot_provider_degraded(self):
        self.assertFalse(fr.spot_provider_degraded("yahoo_chart"))
        self.assertTrue(fr.spot_provider_degraded("stooq"))
        self.assertTrue(fr.spot_provider_degraded(None))


class TestHomeLivePolicy(unittest.TestCase):
    def test_fresh_within_one_hour(self):
        now = datetime.now(timezone.utc)
        f = fr.assess_home_live(captured_at=now, now=now)
        self.assertFalse(f.is_stale)
        self.assertEqual(f.data_class, "home_live")
        self.assertEqual(f.source, "realtime_overlay")
        self.assertEqual(f.policy_max_age_s, 3600.0)

    def test_stale_after_one_hour(self):
        now = datetime.now(timezone.utc)
        f = fr.assess_home_live(
            captured_at=now - timedelta(seconds=3700),
            now=now,
        )
        self.assertTrue(f.is_stale)


if __name__ == "__main__":
    unittest.main()
