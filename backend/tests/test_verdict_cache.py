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

    def test_supabase_read_populates_memory(self):
        payload = _minimal_payload("MSFT")
        session = last_completed_session()
        captured = payload.verdict_captured_at_utc

        class _Query:
            def __init__(self, data):
                self._data = data

            def select(self, *_a, **_k):
                return self

            def eq(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return self

            def execute(self):
                return type("R", (), {"data": self._data})()

        class _Client:
            def table(self, _name):
                return _Query(
                    [
                        {
                            "payload_json": payload.model_dump(mode="json"),
                            "verdict_captured_at_utc": captured,
                        }
                    ]
                )

        with patch.dict(os.environ, {"VERDICT_CACHE_BACKEND": "supabase"}):
            with patch.object(vc, "_supabase_client", return_value=_Client()):
                with patch.object(vc, "overlay_fresh_spot", side_effect=lambda p, **kw: p):
                    hit = vc.get_cached_verdict("MSFT")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.ticker, "MSFT")
        self.assertIn(("MSFT", session), vc._store)

    def test_supabase_write_on_store(self):
        payload = _minimal_payload("NVDA")
        upserted = {}

        class _Query:
            def upsert(self, row):
                upserted.update(row)
                return self

            def execute(self):
                return type("R", (), {"data": [upserted]})()

        class _Client:
            def table(self, _name):
                return _Query()

        with patch.dict(os.environ, {"VERDICT_CACHE_BACKEND": "supabase"}):
            with patch.object(vc, "_supabase_client", return_value=_Client()):
                vc.store_verdict_cache("NVDA", payload)
        self.assertEqual(upserted.get("ticker"), "NVDA")
        self.assertEqual(upserted.get("payload_json", {}).get("ticker"), "NVDA")


class TestVerdictPrewarmTickers(unittest.TestCase):
    def test_default_ticker_list_nonempty(self):
        from backend.verdict_prewarm import PREWARM_DEFAULT_TICKERS

        self.assertGreaterEqual(len(PREWARM_DEFAULT_TICKERS), 15)
        self.assertIn("MSFT", PREWARM_DEFAULT_TICKERS)


class TestConnectorCacheSessionTtl(unittest.TestCase):
    def test_open_session_shorter_ttl(self):
        from backend import connector_cache as cc

        with patch("backend.market_calendar.session_status", return_value="regular"):
            self.assertEqual(cc.connector_cache_ttl(), cc._OPEN_SESSION_TTL)
        with patch("backend.market_calendar.session_status", return_value="closed_weekend"):
            self.assertEqual(cc.connector_cache_ttl(), cc._DEFAULT_TTL)


if __name__ == "__main__":
    unittest.main()
