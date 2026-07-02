"""Offline tests for options flow connector — no live provider calls."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from backend.connectors import options_flow as of
from backend.data_errors import InsufficientDataError


def _sample_chain(source: str = "yahoo") -> dict:
    return {
        "symbol": "AAPL",
        "spot": 150.0,
        "as_of": "2026-07-01T12:00:00+00:00",
        "source": source,
        "partial": False,
        "expirations": [
            {
                "expiry": "2026-07-11",
                "calls": [
                    {"strike": 150, "volume": 1000, "open_interest": 5000, "iv": 0.25, "bid": 3.0, "ask": 3.2, "last": 3.1},
                    {"strike": 155, "volume": 500, "open_interest": 2000, "iv": 0.22, "bid": 1.0, "ask": 1.1, "last": 1.05},
                ],
                "puts": [
                    {"strike": 150, "volume": 1200, "open_interest": 4000, "iv": 0.28, "bid": 2.8, "ask": 3.0, "last": 2.9},
                    {"strike": 145, "volume": 9000, "open_interest": 100, "iv": 0.30, "bid": 1.5, "ask": 1.6, "last": 1.55},
                ],
            }
        ],
    }


class TestOptionsAggregates(unittest.TestCase):
    def test_compute_aggregates(self):
        agg = of.compute_options_aggregates(_sample_chain())
        self.assertEqual(agg["total_call_volume"], 1500)
        self.assertEqual(agg["total_put_volume"], 10200)
        self.assertAlmostEqual(agg["put_call_volume_ratio"], 10200 / 1500, places=3)
        self.assertIsNotNone(agg["iv_atm_call"])
        self.assertIsNotNone(agg["iv_atm_put"])
        self.assertIsNotNone(agg["iv_skew"])
        self.assertTrue(any(u.get("vol_oi_ratio", 0) >= 3 for u in agg["unusual_contracts"]))
        self.assertIn(agg["net_premium_bias"], ("bullish", "bearish", "neutral"))

    def test_compute_options_intelligence(self):
        chain = _sample_chain()
        agg = of.compute_options_aggregates(chain)
        intel = of.compute_options_intelligence(chain, agg)
        self.assertIsNotNone(intel.get("call_oi_pct"))
        self.assertIsNotNone(intel.get("put_oi_pct"))
        self.assertTrue(intel.get("top_call_strikes"))
        self.assertTrue(intel.get("narrative_summary"))
        self.assertIsNotNone(intel.get("expected_move_pct"))

    def test_format_options_flow_for_chat(self):
        chain = _sample_chain()
        agg = of.compute_options_aggregates(chain)
        intel = of.compute_options_intelligence(chain, agg)
        text = of.format_options_flow_for_chat({**agg, **intel, "available": True, "symbol": "AAPL"})
        self.assertIn("Options intelligence for AAPL", text)
        self.assertIn("Bull vs bear", text)

    def test_options_to_brain_overlay(self):
        agg = of.compute_options_aggregates(_sample_chain())
        overlay = of.options_to_brain_overlay(agg)
        self.assertIn("put_call_volume_ratio", overlay)
        self.assertIn("options_net_premium_bias_num", overlay)


    def test_to_legacy_market_intel_payload(self):
        agg = of.compute_options_aggregates(_sample_chain())
        legacy = of.to_legacy_market_intel_payload({**agg, "source": "cboe", "as_of": "t"})
        self.assertIn("spy_put_call_ratio", legacy)
        self.assertIn("signal", legacy)
        self.assertEqual(legacy["put_call_ratio"], legacy["spy_put_call_ratio"])

    def test_fetch_options_flow_sync_disabled(self):
        with patch.dict(os.environ, {"OPTIONS_FLOW_ENABLE": "0"}):
            out = of.fetch_options_flow_sync("SPY")
        self.assertEqual(out.get("error"), "disabled")


class TestOptionsFlowConnector(unittest.TestCase):
    def setUp(self):
        import backend.connector_cache as cc
        cc._store.clear()

    def tearDown(self):
        import backend.connector_cache as cc
        cc._store.clear()

    def test_fallback_yahoo_429_to_cboe(self):
        yahoo_unavail = {"unavailable": True, "reason": "yahoo_429"}
        chain = _sample_chain("cboe")

        async def _run():
            with patch.dict(os.environ, {"OPTIONS_FLOW_ENABLE": "1", "OPTIONS_FLOW_ALLOW_YAHOO": "1"}):
                with patch.object(of, "_fetch_yahoo_options_sync", return_value=yahoo_unavail):
                    with patch.object(of, "_fetch_cboe_options_sync", return_value=chain):
                        return await of.OptionsFlowConnector().fetch_data(ticker="AAPL")

        result = asyncio.run(_run())
        self.assertEqual(result["source"], "cboe")
        self.assertTrue(result["available"])
        self.assertEqual(result["total_call_volume"], 1500)

    def test_all_providers_fail_raises(self):
        unavail = {"unavailable": True, "reason": "down"}

        async def _run():
            with patch.dict(os.environ, {"OPTIONS_FLOW_ENABLE": "1", "OPTIONS_FLOW_ALLOW_YAHOO": "1"}):
                with patch.object(of, "_fetch_yahoo_options_sync", return_value=unavail):
                    with patch.object(of, "_fetch_cboe_options_sync", return_value=unavail):
                        with patch.object(of, "_fetch_nasdaq_options_sync", return_value=unavail):
                            with patch.object(of, "_fetch_alpha_vantage_options_sync", return_value=unavail):
                                return await of.OptionsFlowConnector().fetch_data(ticker="AAPL")

        with self.assertRaises(InsufficientDataError):
            asyncio.run(_run())

    def test_disabled_returns_available_false(self):
        async def _run():
            with patch.dict(os.environ, {"OPTIONS_FLOW_ENABLE": "0"}):
                return await of.OptionsFlowConnector().fetch_data(ticker="AAPL")

        result = asyncio.run(_run())
        self.assertFalse(result.get("available"))


if __name__ == "__main__":
    unittest.main()
