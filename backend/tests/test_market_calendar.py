"""Offline tests for the computed NYSE trading calendar (backend/market_calendar.py).

These lock in two things:
1. The computed holiday rules reproduce the previously hand-maintained 2024-2026
   table exactly (regression guard against the migration).
2. The rules keep working for years past 2026 (the old hardcoded set expired),
   including observance edge cases (Saturday New Year's not observed, etc.).
"""
import unittest
from datetime import date

from backend import market_calendar as mc


class TestHolidaySetMatchesLegacyTable(unittest.TestCase):
    """The retired hardcoded set, reproduced from the rules."""

    LEGACY = {
        2024: {
            date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
            date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2),
            date(2024, 11, 28), date(2024, 12, 25),
        },
        2025: {
            date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
            date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
            date(2025, 11, 27), date(2025, 12, 25),
        },
        2026: {
            date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
            date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
            date(2026, 11, 26), date(2026, 12, 25),
        },
    }

    def test_computed_equals_legacy(self):
        for year, expected in self.LEGACY.items():
            self.assertEqual(set(mc.us_market_holidays(year)), expected, f"year {year}")


class TestGoodFriday(unittest.TestCase):
    def test_good_friday_dates(self):
        self.assertEqual(mc._easter_sunday(2024) - mc.timedelta(days=2), date(2024, 3, 29))
        self.assertEqual(mc._easter_sunday(2025) - mc.timedelta(days=2), date(2025, 4, 18))
        self.assertEqual(mc._easter_sunday(2026) - mc.timedelta(days=2), date(2026, 4, 3))


class TestObservanceEdgeCases(unittest.TestCase):
    def test_saturday_new_year_not_observed(self):
        # Jan 1, 2022 was a Saturday: NYSE did NOT close the preceding Friday.
        self.assertFalse(mc.is_market_holiday(date(2022, 1, 1)))
        self.assertFalse(mc.is_market_holiday(date(2021, 12, 31)))

    def test_sunday_new_year_observed_monday(self):
        # Jan 1, 2023 was a Sunday -> observed Monday Jan 2, 2023.
        self.assertTrue(mc.is_market_holiday(date(2023, 1, 2)))

    def test_saturday_july4_observed_friday(self):
        # Jul 4, 2026 is a Saturday -> observed Friday Jul 3.
        self.assertTrue(mc.is_market_holiday(date(2026, 7, 3)))
        self.assertFalse(mc.is_market_holiday(date(2026, 7, 4)))

    def test_sunday_juneteenth_observed_monday(self):
        # Jun 19, 2022 was a Sunday -> observed Monday Jun 20, 2022.
        self.assertTrue(mc.is_market_holiday(date(2022, 6, 20)))

    def test_juneteenth_not_a_holiday_before_2021(self):
        self.assertFalse(mc.is_market_holiday(date(2019, 6, 19)))


class TestTradingDayHelpers(unittest.TestCase):
    def test_is_trading_day(self):
        self.assertTrue(mc.is_trading_day(date(2026, 6, 12)))   # Friday
        self.assertFalse(mc.is_trading_day(date(2026, 6, 13)))  # Saturday
        self.assertFalse(mc.is_trading_day(date(2026, 6, 14)))  # Sunday
        self.assertFalse(mc.is_trading_day(date(2026, 12, 25)))  # Christmas (Fri)

    def test_previous_trading_day_skips_holiday_weekend(self):
        # Tue after Memorial Day 2026 (Mon May 25) -> prior session is Fri May 22.
        self.assertEqual(mc.previous_trading_day(date(2026, 5, 26)), date(2026, 5, 22))

    def test_adjust_to_trading_day(self):
        self.assertEqual(mc.adjust_to_trading_day(date(2026, 6, 13)), date(2026, 6, 12))  # Sat -> Fri
        self.assertEqual(mc.adjust_to_trading_day(date(2026, 12, 25)), date(2026, 12, 24))  # Christmas -> Thu
        self.assertEqual(mc.adjust_to_trading_day(date(2026, 6, 12)), date(2026, 6, 12))  # itself


class TestLastCompletedSession(unittest.TestCase):
    def test_explicit_weekend_and_holiday(self):
        self.assertEqual(mc.last_completed_session(date(2026, 6, 13)), date(2026, 6, 12))
        self.assertEqual(mc.last_completed_session(date(2026, 6, 14)), date(2026, 6, 12))
        self.assertEqual(mc.last_completed_session(date(2026, 12, 25)), date(2026, 12, 24))
        self.assertEqual(mc.last_completed_session(date(2026, 6, 12)), date(2026, 6, 12))


class TestSessionStatus(unittest.TestCase):
    def _et(self, y, m, d, hh, mm):
        try:
            from zoneinfo import ZoneInfo
            return mc.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))
        except Exception:
            self.skipTest("zoneinfo unavailable")

    def test_regular_session(self):
        self.assertEqual(mc.session_status(self._et(2026, 6, 12, 10, 0)), mc.SESSION_REGULAR)
        self.assertTrue(mc.is_market_open(self._et(2026, 6, 12, 10, 0)))

    def test_pre_and_post_market(self):
        self.assertEqual(mc.session_status(self._et(2026, 6, 12, 8, 0)), mc.SESSION_PRE_MARKET)
        self.assertEqual(mc.session_status(self._et(2026, 6, 12, 17, 0)), mc.SESSION_POST_MARKET)
        self.assertFalse(mc.is_market_open(self._et(2026, 6, 12, 8, 0)))

    def test_weekend_and_holiday_closed(self):
        self.assertEqual(mc.session_status(self._et(2026, 6, 13, 10, 0)), mc.SESSION_CLOSED_WEEKEND)
        self.assertEqual(mc.session_status(self._et(2026, 12, 25, 10, 0)), mc.SESSION_CLOSED_HOLIDAY)
        self.assertFalse(mc.is_market_open(self._et(2026, 12, 25, 10, 0)))


class TestFutureProof(unittest.TestCase):
    def test_far_future_year_has_full_holiday_set(self):
        # The old hardcoded set ended in 2026; rules must still produce holidays.
        h2030 = mc.us_market_holidays(2030)
        self.assertEqual(len(h2030), 10)
        self.assertIn(date(2030, 1, 1), h2030)   # Jan 1, 2030 is a Tuesday
        self.assertIn(date(2030, 12, 25), h2030)  # Dec 25, 2030 is a Wednesday


if __name__ == "__main__":
    unittest.main()
