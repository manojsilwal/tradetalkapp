"""Data layer: token bucket, completeness reviewer, fallback router, backfill."""
import unittest

from backend.brain.data import provider as P


class _FullPriceProvider(P.ProviderAdapter):
    name = "full"
    capabilities = {"price"}

    def _fetch(self, ticker, data_type):
        return {"ticker": ticker, "date": "2026-06-22", "close": 100.0, "volume": 1_000}


class _MissingVolumeProvider(P.ProviderAdapter):
    name = "missing_vol"
    capabilities = {"price"}

    def _fetch(self, ticker, data_type):
        return {"ticker": ticker, "date": "2026-06-22", "close": 100.0}  # no volume


class _BadPriceProvider(P.ProviderAdapter):
    name = "bad"
    capabilities = {"price"}

    def _fetch(self, ticker, data_type):
        return {"ticker": ticker, "date": "2026-06-22", "close": -5.0, "volume": 1}


class _ErroringProvider(P.ProviderAdapter):
    name = "boom"
    capabilities = {"price"}

    def _fetch(self, ticker, data_type):
        raise P.ProviderError("simulated outage")


class TestTokenBucket(unittest.TestCase):
    def test_pacing_with_injected_clock(self):
        clock = [0.0]
        tb = P.TokenBucket(rate_per_sec=1.0, capacity=2.0, time_fn=lambda: clock[0])
        self.assertTrue(tb.try_acquire())
        self.assertTrue(tb.try_acquire())
        self.assertFalse(tb.try_acquire())  # bucket empty
        self.assertAlmostEqual(tb.time_until_available(), 1.0, places=3)
        clock[0] = 1.0  # 1 second passes -> 1 token refilled
        self.assertTrue(tb.try_acquire())


class TestCompletenessReviewer(unittest.TestCase):
    def setUp(self):
        self.rev = P.CompletenessReviewer()

    def test_complete_record(self):
        r = self.rev.review({"ticker": "A", "date": "d", "close": 10, "volume": 5}, "price")
        self.assertTrue(r["complete"])

    def test_missing_field(self):
        r = self.rev.review({"ticker": "A", "date": "d", "close": 10}, "price")
        self.assertFalse(r["complete"])
        self.assertIn("volume", r["missing"])

    def test_sanity_bounds(self):
        r = self.rev.review({"ticker": "A", "date": "d", "close": -1, "volume": 5}, "price")
        self.assertFalse(r["complete"])
        self.assertTrue(any("close" in i for i in r["issues"]))

    def test_reconcile(self):
        ok = self.rev.reconcile([{"close": 100.0}, {"close": 100.5}], "close", rel_tol=0.02)
        self.assertTrue(ok["agree"])
        bad = self.rev.reconcile([{"close": 100.0}, {"close": 150.0}], "close", rel_tol=0.02)
        self.assertFalse(bad["agree"])


class TestRouter(unittest.TestCase):
    def test_falls_back_to_complete_provider(self):
        router = P.ProviderRouter({"price": [_MissingVolumeProvider(), _FullPriceProvider()]})
        res = router.get("AAPL", "price")
        self.assertTrue(res["complete"])
        self.assertEqual(res["provider"], "full")
        self.assertEqual(len(res["attempts"]), 2)
        self.assertEqual(res["attempts"][0]["status"], "incomplete")

    def test_skips_errors_and_bad_data(self):
        router = P.ProviderRouter(
            {"price": [_ErroringProvider(), _BadPriceProvider(), _FullPriceProvider()]}
        )
        res = router.get("MSFT", "price")
        self.assertTrue(res["complete"])
        self.assertEqual(res["provider"], "full")
        statuses = [a["status"] for a in res["attempts"]]
        self.assertEqual(statuses, ["error", "incomplete", "complete"])

    def test_all_fail_returns_incomplete(self):
        router = P.ProviderRouter({"price": [_MissingVolumeProvider()]})
        res = router.get("X", "price")
        self.assertFalse(res["complete"])
        self.assertIsNone(res["provider"])

    def test_rate_limited_provider_is_skipped(self):
        clock = [0.0]
        limited = _FullPriceProvider(
            bucket=P.TokenBucket(rate_per_sec=0.0, capacity=0.0, time_fn=lambda: clock[0]))
        backup = _FullPriceProvider()
        backup.name = "backup"
        router = P.ProviderRouter({"price": [limited, backup]})
        res = router.get("X", "price")
        self.assertTrue(res["complete"])
        self.assertEqual(res["provider"], "backup")
        self.assertEqual(res["attempts"][0]["status"], "rate_limited")


class TestBackfill(unittest.TestCase):
    def test_runs_to_completion_with_progress(self):
        router = P.ProviderRouter({"price": [_FullPriceProvider()]})
        seen = []
        res = P.run_backfill(["A", "B", "C"], ["price"], router,
                             progress_cb=lambda ev: seen.append(ev["pct"]))
        self.assertTrue(res.complete)
        self.assertEqual(res.done, 3)
        self.assertEqual(res.pct, 100.0)
        self.assertEqual(seen[-1], 100.0)

    def test_resumable_from_checkpoint(self):
        router = P.ProviderRouter({"price": [_FullPriceProvider()]})
        res = P.run_backfill(["A", "B", "C", "D"], ["price"], router,
                             checkpoint={"A:price", "B:price"})
        self.assertTrue(res.complete)
        self.assertEqual(res.done, 4)
        # already-done keys were not re-provider'd this run
        self.assertNotIn("A:price", res.provider_used)
        self.assertIn("C:price", res.provider_used)

    def test_records_failures(self):
        router = P.ProviderRouter({"price": [_MissingVolumeProvider()]})
        res = P.run_backfill(["A", "B"], ["price"], router)
        self.assertFalse(res.complete)
        self.assertEqual(set(res.failures), {"A:price", "B:price"})


if __name__ == "__main__":
    unittest.main()
