"""Offline tests for per-trading-day decision-terminal verdict cache."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend import verdict_cache as vc
from backend.market_calendar import last_completed_session
from backend.schemas import (
    DecisionTerminalPayload,
    TerminalQualityPanel,
    TerminalRoadmapPanel,
    TerminalValuationPanel,
    TerminalVerdictPanel,
    TerminalFieldProvenance,
)


def _minimal_payload(ticker: str = "AAPL") -> DecisionTerminalPayload:
    return DecisionTerminalPayload(
        ticker=ticker,
        disclaimer="d",
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        verdict_captured_at_utc=datetime.now(timezone.utc).isoformat(),
        valuation=TerminalValuationPanel(
            current_price_usd=100.0,
            average_fair_value_usd=95.0,
            pct_vs_average=5.0,
            gauge_label="Fair",
            models=[],
        ),
        quality=TerminalQualityPanel(rows=[]),
        verdict=TerminalVerdictPanel(
            headline_verdict="BUY",
            debate_verdict="BUY",
            swarm_verdict="BUY",
        ),
        roadmap=TerminalRoadmapPanel(
            confidence_0_1=0.5,
            provenance=TerminalFieldProvenance(),
        ),
    )


class TestVerdictCache(unittest.TestCase):
    def setUp(self):
        vc.clear_verdict_cache()
        os.environ["VERDICT_CACHE_ENABLE"] = "1"

    def tearDown(self):
        vc.clear_verdict_cache()

    def test_store_and_hit_same_session(self):
        payload = _minimal_payload()
        vc.store_verdict_cache("AAPL", payload)
        with patch.object(vc, "overlay_fresh_spot", side_effect=lambda p, **kw: p) as overlay:
            hit = vc.get_cached_verdict("AAPL")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.ticker, "AAPL")
        overlay.assert_called_once()

    def test_miss_different_ticker(self):
        vc.store_verdict_cache("AAPL", _minimal_payload())
        self.assertIsNone(vc.get_cached_verdict("MSFT"))

    def test_disabled_when_env_off(self):
        os.environ["VERDICT_CACHE_ENABLE"] = "0"
        vc.store_verdict_cache("AAPL", _minimal_payload())
        self.assertIsNone(vc.get_cached_verdict("AAPL"))

    def test_overlay_marks_from_cache(self):
        from backend.connectors.spot import SpotQuote

        payload = _minimal_payload()
        with patch("backend.connectors.spot.resolve_spot") as rs:
            rs.return_value = SpotQuote(
                price=101.5,
                source="yahoo_chart",
                captured_at_utc=datetime.now(timezone.utc).isoformat(),
                degraded=False,
            )
            out = vc.overlay_fresh_spot(payload, verdict_captured_at_utc=payload.verdict_captured_at_utc)
        self.assertTrue(out.verdict_from_cache)
        self.assertEqual(out.spot.price_usd, 101.5)

    def test_session_key_uses_last_completed_session(self):
        payload = _minimal_payload()
        vc.store_verdict_cache("AAPL", payload)
        key = ("AAPL", last_completed_session())
        self.assertIn(key, vc._store)


class TestConnectorCacheSessionTtl(unittest.TestCase):
    def test_open_session_shorter_ttl(self):
        from backend import connector_cache as cc

        with patch("backend.market_calendar.session_status", return_value="regular"):
            self.assertEqual(cc.connector_cache_ttl(), cc._OPEN_SESSION_TTL)
        with patch("backend.market_calendar.session_status", return_value="closed_weekend"):
            self.assertEqual(cc.connector_cache_ttl(), cc._DEFAULT_TTL)


if __name__ == "__main__":
    unittest.main()
