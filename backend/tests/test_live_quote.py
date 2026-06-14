"""Offline tests for hedged live-quote engine (MCP /mcp/sp500/live-quote)."""
import asyncio
import unittest
from unittest import mock

from backend.connectors import live_quote


def _run(coro):
    return asyncio.run(coro)


class TestLiveQuoteEngine(unittest.TestCase):
    def setUp(self):
        live_quote._CACHE.clear()

    def test_universe_rejection(self):
        with mock.patch.object(live_quote, "_sp500_universe", return_value=frozenset({"AAPL"})):
            with self.assertRaises(ValueError):
                _run(live_quote.get_live_quote("ZZZZ"))

    def test_primary_success_no_lake(self):
        row = live_quote._row("AAPL", price=190.5, change_pct=1.2, previous_close=188.0, source="yahoo_fast_info")

        async def _fake_hedged(sym):
            return row

        with mock.patch.object(live_quote, "_sp500_universe", return_value=frozenset({"AAPL"})), \
                mock.patch.object(live_quote, "_hedged_live_fetch", side_effect=_fake_hedged):
            payload, fresh = _run(live_quote.get_live_quote("AAPL"))

        self.assertEqual(payload["price"], 190.5)
        self.assertEqual(payload["source"], "yahoo_fast_info")
        self.assertFalse(fresh.degraded)
        self.assertFalse(fresh.is_stale)

    def test_fallback_provider_marked_degraded(self):
        row = live_quote._row("AAPL", price=50.0, source="stooq")

        async def _fake_hedged(sym):
            return row

        with mock.patch.object(live_quote, "_sp500_universe", return_value=frozenset({"AAPL"})), \
                mock.patch.object(live_quote, "_hedged_live_fetch", side_effect=_fake_hedged):
            payload, fresh = _run(live_quote.get_live_quote("AAPL"))

        self.assertEqual(payload["source"], "stooq")
        self.assertTrue(fresh.degraded)

    def test_all_live_fail_uses_lake_eod(self):
        async def _fake_hedged(sym):
            return None

        with mock.patch.object(live_quote, "_sp500_universe", return_value=frozenset({"AAPL"})), \
                mock.patch.object(live_quote, "_hedged_live_fetch", side_effect=_fake_hedged), \
                mock.patch.object(
                    live_quote,
                    "latest_close_from_lake",
                    return_value={"trade_date": "2024-01-02", "close": 185.0},
                ):
            payload, fresh = _run(live_quote.get_live_quote("AAPL"))

        self.assertEqual(payload["price"], 185.0)
        self.assertEqual(payload["source"], "data_lake")
        # Session policy: Jan 2024 vs real last session => stale
        self.assertTrue(fresh.is_stale)

    def test_all_sources_fail_price_none(self):
        async def _fake_hedged(sym):
            return None

        with mock.patch.object(live_quote, "_sp500_universe", return_value=frozenset({"AAPL"})), \
                mock.patch.object(live_quote, "_hedged_live_fetch", side_effect=_fake_hedged), \
                mock.patch.object(live_quote, "latest_close_from_lake", return_value=None):
            payload, fresh = _run(live_quote.get_live_quote("AAPL"))

        self.assertIsNone(payload["price"])
        self.assertTrue(fresh.is_stale)

    def test_cache_hit_skips_fetch(self):
        row = live_quote._row("AAPL", price=100.0, source="yahoo_fast_info")
        fresh = live_quote._stamp_live("yahoo_fast_info")
        live_quote._cache_put("AAPL", row, fresh)

        with mock.patch.object(live_quote, "_hedged_live_fetch") as hedged:
            payload, _ = _run(live_quote.get_live_quote("AAPL"))
            hedged.assert_not_called()

        self.assertEqual(payload["price"], 100.0)

    def test_hedged_primary_wins_before_fanout(self):
        sym = "AAPL"
        primary_row = live_quote._row(sym, price=200.0, source="yahoo_fast_info")

        async def _slow_primary(name, s):
            if name == live_quote._PRIMARY:
                await asyncio.sleep(0.05)
                return primary_row
            return live_quote._row(s, price=1.0, source="stooq")

        with mock.patch.object(live_quote, "_hedge_delay_sec", return_value=0.2), \
                mock.patch.object(live_quote, "_hard_deadline_sec", return_value=2.0), \
                mock.patch.object(live_quote, "_fetch_one_provider", side_effect=_slow_primary), \
                mock.patch.object(live_quote, "_parallel_fallbacks", return_value=["stooq"]):
            row = _run(live_quote._hedged_live_fetch(sym))

        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "yahoo_fast_info")

    def test_hedged_fanout_when_primary_slow(self):
        sym = "AAPL"
        fb_row = live_quote._row(sym, price=199.0, source="stooq")

        call_count = {"n": 0}

        async def _primary_slow(name, s):
            if name == live_quote._PRIMARY:
                await asyncio.sleep(1.0)
                return None
            call_count["n"] += 1
            return fb_row

        with mock.patch.object(live_quote, "_hedge_delay_sec", return_value=0.05), \
                mock.patch.object(live_quote, "_hard_deadline_sec", return_value=2.0), \
                mock.patch.object(live_quote, "_fetch_one_provider", side_effect=_primary_slow), \
                mock.patch.object(live_quote, "_parallel_fallbacks", return_value=["stooq"]):
            row = _run(live_quote._hedged_live_fetch(sym))

        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "stooq")

    def test_fincrawler_last_after_parallel_fallbacks_fail(self):
        sym = "AAPL"
        fc_row = live_quote._row(sym, price=198.0, source="fincrawler")

        async def _fetch(name, s):
            if name == live_quote._PRIMARY:
                await asyncio.sleep(1.0)
                return None
            if name == "stooq":
                return None
            if name == "fincrawler":
                return fc_row
            return None

        with mock.patch.object(live_quote, "_hedge_delay_sec", return_value=0.05), \
                mock.patch.object(live_quote, "_hard_deadline_sec", return_value=2.0), \
                mock.patch.object(live_quote, "_fetch_one_provider", side_effect=_fetch), \
                mock.patch.object(live_quote, "_parallel_fallbacks", return_value=["stooq"]), \
                mock.patch.object(live_quote, "_fincrawler_enabled", return_value=True):
            row = _run(live_quote._hedged_live_fetch(sym))

        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "fincrawler")


if __name__ == "__main__":
    unittest.main()
