"""Offline tests for the truthful-data freshness signal on the daily brief."""
import unittest
from datetime import date
from unittest import mock

from backend import daily_brief


class TestExpectedLastSession(unittest.TestCase):
    def test_saturday_rolls_back_to_friday(self):
        # 2026-06-13 is a Saturday; last completed session is Fri 2026-06-12.
        self.assertEqual(
            daily_brief.expected_last_session(date(2026, 6, 13)),
            date(2026, 6, 12),
        )

    def test_sunday_rolls_back_to_friday(self):
        self.assertEqual(
            daily_brief.expected_last_session(date(2026, 6, 14)),
            date(2026, 6, 12),
        )

    def test_holiday_weekday_is_skipped(self):
        # 2026-12-25 (Christmas) is a Friday holiday -> skip to Thu 2026-12-24.
        self.assertEqual(
            daily_brief.expected_last_session(date(2026, 12, 25)),
            date(2026, 12, 24),
        )

    def test_plain_weekday_returns_itself(self):
        # Friday 2026-06-12, not a holiday.
        self.assertEqual(
            daily_brief.expected_last_session(date(2026, 6, 12)),
            date(2026, 6, 12),
        )


class TestComputeDataFreshness(unittest.TestCase):
    def test_two_year_old_data_is_stale(self):
        f = daily_brief.compute_data_freshness(date(2024, 1, 2), today=date(2026, 6, 13))
        self.assertTrue(f["is_stale"])
        self.assertEqual(f["db_latest_date"], "2024-01-02")
        self.assertEqual(f["expected_last_session"], "2026-06-12")
        self.assertGreater(f["staleness_days"], 800)

    def test_current_data_is_fresh(self):
        f = daily_brief.compute_data_freshness(date(2026, 6, 12), today=date(2026, 6, 13))
        self.assertFalse(f["is_stale"])
        self.assertEqual(f["staleness_days"], 0)

    def test_missing_data_is_stale(self):
        f = daily_brief.compute_data_freshness(None, today=date(2026, 6, 13))
        self.assertTrue(f["is_stale"])
        self.assertIsNone(f["staleness_days"])
        self.assertIsNone(f["db_latest_date"])


class TestBuildDailyBriefBoth(unittest.TestCase):
    def setUp(self):
        # enrichment hits yfinance; stub it out for offline tests.
        self._enrich = mock.patch.object(daily_brief, "enrich_daily_brief_rows", lambda rows: None)
        self._enrich.start()
        self.addCleanup(self._enrich.stop)
        # Stored data is ~2.5 years stale.
        self._latest = mock.patch.object(daily_brief, "get_latest_trade_date", lambda: date(2024, 1, 2))
        self._latest.start()
        self.addCleanup(self._latest.stop)
        self._expected = mock.patch.object(daily_brief, "expected_last_session", lambda today=None: date(2026, 6, 12))
        self._expected.start()
        self.addCleanup(self._expected.stop)

    def test_stale_store_prefers_live(self):
        live_rows = [
            {"bucket": "gainer", "symbol": "AAA", "is_compelling": False},
            {"bucket": "loser", "symbol": "BBB", "is_compelling": False},
        ]
        with mock.patch.object(daily_brief, "_fetch_movers_from_intel", lambda nl, ng: live_rows):
            payload = daily_brief.build_daily_brief()
        self.assertEqual(payload["source"], "market_intel_live")
        self.assertEqual(payload["trade_date"], "2026-06-12")
        self.assertFalse(payload["data_freshness"]["is_stale"])

    def test_live_failure_returns_empty_movers_not_stale_rows(self):
        stored = {
            "trade_date": "2024-01-02",
            "source": "snapshot",
            "rows": [{"bucket": "gainer", "symbol": "AAA", "is_compelling": False}],
            "losers": [],
            "gainers": [],
            "compelling": [],
        }

        def _boom(nl, ng):
            raise RuntimeError("yfinance unavailable")

        with mock.patch.object(daily_brief, "_fetch_movers_from_intel", _boom), \
                mock.patch.object(daily_brief, "load_snapshot", lambda td: dict(stored)):
            payload = daily_brief.build_daily_brief()
        self.assertEqual(payload["trade_date"], "2026-06-12")
        self.assertTrue(payload["data_freshness"]["is_stale"])
        self.assertTrue(payload.get("stale_unavailable"))
        self.assertEqual(payload.get("losers"), [])
        self.assertEqual(payload.get("gainers"), [])


class TestOverlayRealtimeQuotesForce(unittest.TestCase):
    def test_force_overlays_quotes(self):
        payload = {
            "rows": [{"symbol": "AAPL", "daily_return_pct": 0.0}],
            "losers": [],
            "gainers": [],
            "compelling": [],
        }
        quotes = {"AAPL": {"price": 200.0, "pct": 1.5, "previous_close": 197.0}}
        with mock.patch("backend.market_intel.fetch_realtime_quotes", return_value=quotes):
            out = daily_brief.overlay_realtime_quotes(payload, force=True)
        self.assertTrue(out["realtime_overlay"])
        self.assertEqual(out["rows"][0]["daily_return_pct"], 1.5)

    def test_force_empty_quotes_no_overlay(self):
        payload = {
            "rows": [{"symbol": "AAPL", "daily_return_pct": 0.0}],
            "losers": [],
            "gainers": [],
        }
        with mock.patch("backend.market_intel.fetch_realtime_quotes", return_value={}):
            out = daily_brief.overlay_realtime_quotes(payload, force=True)
        self.assertFalse(out["realtime_overlay"])


class TestResortMoversByLiveReturn(unittest.TestCase):
    def test_gainers_resorted_desc_with_ranks(self):
        payload = {
            "gainers": [
                {"symbol": "META", "daily_return_pct": 4.25, "rank": 1},
                {"symbol": "MRNA", "daily_return_pct": 4.28, "rank": 2},
                {"symbol": "ETSY", "daily_return_pct": 4.62, "rank": 3},
                {"symbol": "NONE", "daily_return_pct": None, "rank": 4},
            ],
            "losers": [
                {"symbol": "AAA", "daily_return_pct": -1.0, "rank": 1},
                {"symbol": "BBB", "daily_return_pct": -5.0, "rank": 2},
            ],
        }
        daily_brief._resort_movers_by_live_return(payload)
        self.assertEqual(
            [r["symbol"] for r in payload["gainers"]],
            ["ETSY", "MRNA", "META", "NONE"],
        )
        self.assertEqual([r["rank"] for r in payload["gainers"]], [1, 2, 3, 4])
        self.assertEqual(
            [r["symbol"] for r in payload["losers"]],
            ["BBB", "AAA"],
        )

    def test_overlay_resorts_gainers(self):
        payload = {
            "rows": [
                {"symbol": "META", "daily_return_pct": 4.25, "bucket": "gainer"},
                {"symbol": "ETSY", "daily_return_pct": 4.62, "bucket": "gainer"},
            ],
            "gainers": [
                {"symbol": "META", "daily_return_pct": 4.25, "rank": 1},
                {"symbol": "ETSY", "daily_return_pct": 4.62, "rank": 2},
            ],
            "losers": [],
        }
        quotes = {
            "META": {"price": 700.0, "pct": 4.25, "previous_close": 671.0},
            "ETSY": {"price": 80.0, "pct": 6.5, "previous_close": 75.1},
        }
        with mock.patch("backend.market_intel.fetch_realtime_quotes", return_value=quotes):
            out = daily_brief.overlay_realtime_quotes(payload, force=True)
        self.assertEqual(out["gainers"][0]["symbol"], "ETSY")
        self.assertEqual(out["gainers"][0]["rank"], 1)


class TestMorningBriefHomeLive(unittest.TestCase):
    def test_apply_overlay_stamps_home_live(self):
        from backend.morning_brief import _apply_home_live_overlay

        payload = {
            "summary": {"benchmark_context": {}, "daily_return_pct": 0.0},
            "impact_movers": [{"symbol": "AAPL", "daily_return_pct": 0.0}],
        }
        enriched = [{"ticker": "AAPL", "current_value": 1000.0}]
        quotes = {
            "AAPL": {"price": 200.0, "pct": 1.2, "previous_close": 197.6},
            "SPY": {"price": 500.0, "pct": 0.5, "previous_close": 497.5},
        }
        with mock.patch("backend.market_intel.fetch_realtime_quotes", return_value=quotes):
            out = _apply_home_live_overlay(
                payload, enriched=enriched, total_value=1000.0
            )
        self.assertTrue(out["realtime_overlay"])
        self.assertEqual(out["data_freshness"]["data_class"], "home_live")
        self.assertFalse(out["data_freshness"]["is_stale"])
        self.assertEqual(out["impact_movers"][0]["daily_return_pct"], 1.2)
        self.assertEqual(out["summary"]["daily_return_pct"], 1.2)

    def test_apply_overlay_failure_falls_back_session_stale(self):
        from backend.morning_brief import _apply_home_live_overlay

        payload = {"summary": {}, "impact_movers": []}
        with mock.patch("backend.market_intel.fetch_realtime_quotes", return_value={}), \
                mock.patch("backend.daily_brief.get_latest_trade_date", return_value=date(2024, 1, 2)), \
                mock.patch("backend.daily_brief.expected_last_session", return_value=date(2026, 6, 12)):
            out = _apply_home_live_overlay(
                payload, enriched=[{"ticker": "AAPL", "current_value": 100}], total_value=100
            )
        self.assertFalse(out["realtime_overlay"])
        self.assertTrue(out["data_freshness"]["is_stale"])


if __name__ == "__main__":
    unittest.main()
