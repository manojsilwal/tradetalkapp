"""Offline tests for the layered 13F security mapper (no live OpenFIGI)."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock


class SecurityMapperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        os.environ["FUND_LEADERBOARD_DB_PATH"] = os.path.join(cls._tmpdir.name, "fl.db")
        from backend import fund_leaderboard_store as store
        if hasattr(store._local, "conn"):
            del store._local.conn
        cls.store = store
        store.init_schema()

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def setUp(self):
        from backend.coral_skills import security_mapper
        self.mapper = security_mapper
        # Drop the lru cache so each test controls the static map via patching.
        security_mapper._static_cusip_map.cache_clear()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_static_cusip_hit_skips_openfigi(self):
        m = self.mapper
        with mock.patch.object(m, "_static_cusip_map", return_value={
            "037833100": {"ticker": "AAPL", "name": "APPLE INC", "sector": "Technology"},
        }), mock.patch.object(m, "_openfigi_resolve", new=mock.AsyncMock(return_value={})) as figi:
            out = self._run(m.map_holdings_to_tickers([
                {"cusip": "037833100", "issuer_name": "APPLE INC", "market_value_usd": 100.0},
            ]))
        self.assertEqual(out[0]["ticker"], "AAPL")
        self.assertEqual(out[0]["sector"], "Technology")
        self.assertEqual(out[0]["mapping_status"], "mapped_static")
        figi.assert_not_awaited()

    def test_issuer_fallback_when_cusip_unknown(self):
        m = self.mapper
        with mock.patch.object(m, "_static_cusip_map", return_value={}), \
             mock.patch.object(m, "_openfigi_resolve", new=mock.AsyncMock(return_value={})) as figi:
            out = self._run(m.map_holdings_to_tickers([
                {"cusip": "ZZZUNKNOWN", "issuer_name": "MICROSOFT CORP", "market_value_usd": 50.0},
            ]))
        self.assertEqual(out[0]["ticker"], "MSFT")
        self.assertEqual(out[0]["mapping_status"], "mapped_issuer")
        # OpenFIGI is only called for CUSIPs that issuer fallback could not resolve.
        figi.assert_not_awaited()

    def test_openfigi_used_only_for_remaining_misses(self):
        m = self.mapper
        figi_mock = mock.AsyncMock(return_value={"99999XYZ9": {"ticker": "NVDA", "name": "NVIDIA CORP"}})
        with mock.patch.object(m, "_static_cusip_map", return_value={}), \
             mock.patch.object(m, "_openfigi_resolve", new=figi_mock):
            out = self._run(m.map_holdings_to_tickers([
                {"cusip": "99999XYZ9", "issuer_name": "Some Unmatchable Issuer 4242", "market_value_usd": 10.0},
            ]))
        self.assertEqual(out[0]["ticker"], "NVDA")
        self.assertEqual(out[0]["mapping_status"], "mapped_openfigi")
        figi_mock.assert_awaited_once()
        # The CUSIP that hit OpenFIGI should be cached for next time.
        cached = self.store.cache_get_ticker("99999XYZ9")
        self.assertEqual(cached["ticker"], "NVDA")

    def test_unmapped_holding_marked_and_cached(self):
        m = self.mapper
        with mock.patch.object(m, "_static_cusip_map", return_value={}), \
             mock.patch.object(m, "_openfigi_resolve", new=mock.AsyncMock(return_value={"NOPE000": None})):
            out = self._run(m.map_holdings_to_tickers([
                {"cusip": "NOPE000", "issuer_name": "Definitely Not Real 9988", "market_value_usd": 5.0},
            ]))
        self.assertIsNone(out[0]["ticker"])
        self.assertEqual(out[0]["mapping_status"], "unmapped")

    def test_openfigi_429_retry_does_not_drop_cusips(self):
        m = self.mapper

        class FakeResp:
            def __init__(self, status, payload=None):
                self.status_code = status
                self._payload = payload or []

            def json(self):
                return self._payload

            def raise_for_status(self):
                pass

        # First call 429, second call succeeds — the batch must not be dropped.
        responses = [
            FakeResp(429),
            FakeResp(200, [{"data": [{"ticker": "AAPL", "exchCode": "US", "name": "APPLE INC"}]}]),
        ]

        class FakeClient:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def post(self_inner, *a, **k):
                return responses.pop(0)

        with mock.patch.object(m.httpx, "AsyncClient", return_value=FakeClient()), \
             mock.patch.object(m.asyncio, "sleep", new=mock.AsyncMock()):
            resolved = self._run(m._openfigi_resolve(["037833100"]))
        self.assertEqual(resolved["037833100"]["ticker"], "AAPL")


if __name__ == "__main__":
    unittest.main()
