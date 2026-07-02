"""Offline tests for per-trading-day decision-terminal slice cache."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend import verdict_cache as vc
from backend.market_calendar import last_completed_session
from backend.schemas import (
    DebateResult,
    DecisionRoadmapPayload,
    DecisionSnapshotPayload,
    DecisionTerminalPayload,
    DecisionVerdictPayload,
    SwarmConsensus,
    MarketState,
    MarketRegime,
    TerminalQualityPanel,
    TerminalRoadmapPanel,
    TerminalValuationPanel,
    TerminalVerdictPanel,
    TerminalFieldProvenance,
)


def _minimal_snapshot(ticker: str = "AAPL") -> DecisionSnapshotPayload:
    now = datetime.now(timezone.utc).isoformat()
    return DecisionSnapshotPayload(
        ticker=ticker,
        disclaimer="d",
        generated_at_utc=now,
        valuation=TerminalValuationPanel(
            current_price_usd=100.0,
            average_fair_value_usd=95.0,
            pct_vs_average=5.0,
            gauge_label="Fair",
            models=[],
        ),
        quality=TerminalQualityPanel(rows=[]),
    )


def _minimal_verdict(ticker: str = "AAPL") -> DecisionVerdictPayload:
    now = datetime.now(timezone.utc).isoformat()
    swarm = SwarmConsensus(
        ticker=ticker,
        macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
        global_signal=1,
        global_verdict="BUY",
        confidence=0.7,
        factors={},
    )
    debate = DebateResult(
        ticker=ticker,
        arguments=[],
        verdict="BUY",
        consensus_confidence=0.8,
        moderator_summary="",
        bull_score=1,
        bear_score=1,
        neutral_score=1,
    )
    return DecisionVerdictPayload(
        ticker=ticker,
        generated_at_utc=now,
        verdict_captured_at_utc=now,
        verdict=TerminalVerdictPanel(
            headline_verdict="BUY",
            debate_verdict="BUY",
            swarm_verdict="BUY",
        ),
        swarm=swarm,
        debate=debate,
    )


def _minimal_roadmap(ticker: str = "AAPL") -> DecisionRoadmapPayload:
    return DecisionRoadmapPayload(
        ticker=ticker,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        roadmap=TerminalRoadmapPanel(
            confidence_0_1=0.5,
            provenance=TerminalFieldProvenance(),
        ),
        current_price_usd=100.0,
    )


def _minimal_payload(ticker: str = "AAPL") -> DecisionTerminalPayload:
    swarm = SwarmConsensus(
        ticker=ticker,
        macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
        global_signal=1,
        global_verdict="BUY",
        confidence=0.7,
        factors={},
    )
    debate = DebateResult(
        ticker=ticker,
        arguments=[],
        verdict="BUY",
        consensus_confidence=0.8,
        moderator_summary="",
        bull_score=1,
        bear_score=1,
        neutral_score=1,
    )
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
        swarm=swarm,
        debate=debate,
    )


class TestVerdictCache(unittest.TestCase):
    def setUp(self):
        vc.clear_verdict_cache()
        os.environ["VERDICT_CACHE_ENABLE"] = "1"

    def tearDown(self):
        vc.clear_verdict_cache()

    def test_slice_store_and_hit(self):
        vc.store_slice_cache(vc.SLICE_SNAPSHOT, "AAPL", _minimal_snapshot())
        with patch.object(vc, "overlay_fresh_spot_on_snapshot", side_effect=lambda p: p):
            hit = vc.get_cached_slice(vc.SLICE_SNAPSHOT, "AAPL")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.ticker, "AAPL")

    def test_store_and_hit_full_assembly(self):
        vc.store_verdict_cache("AAPL", _minimal_payload())
        with patch.object(vc, "overlay_fresh_spot_on_snapshot", side_effect=lambda p: p):
            hit = vc.get_cached_verdict("AAPL")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.ticker, "AAPL")

    def test_miss_different_ticker(self):
        vc.store_slice_cache(vc.SLICE_VERDICT, "AAPL", _minimal_verdict())
        self.assertIsNone(vc.get_cached_slice(vc.SLICE_VERDICT, "MSFT"))

    def test_disabled_when_env_off(self):
        os.environ["VERDICT_CACHE_ENABLE"] = "0"
        vc.store_slice_cache(vc.SLICE_ROADMAP, "AAPL", _minimal_roadmap())
        self.assertIsNone(vc.get_cached_slice(vc.SLICE_ROADMAP, "AAPL"))

    def test_overlay_marks_snapshot_from_cache(self):
        from backend.connectors.spot import SpotQuote

        payload = _minimal_snapshot()
        with patch("backend.connectors.spot.resolve_spot") as rs:
            rs.return_value = SpotQuote(
                price=101.5,
                source="yahoo_chart",
                captured_at_utc=datetime.now(timezone.utc).isoformat(),
                degraded=False,
            )
            out = vc.overlay_fresh_spot_on_snapshot(payload)
        self.assertTrue(out.slice_from_cache)
        self.assertEqual(out.spot.price_usd, 101.5)

    def test_overlay_marks_full_from_cache(self):
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

    def test_roadmap_overlay_updates_current_price(self):
        from backend.connectors.spot import SpotQuote

        now = datetime.now(timezone.utc).isoformat()
        payload = DecisionRoadmapPayload(
            ticker="AAPL",
            generated_at_utc=now,
            roadmap=TerminalRoadmapPanel(
                bull_price_usd=200.0,
                base_price_usd=170.0,
                bear_price_usd=140.0,
                used_heuristic_fallback=True,
            ),
            current_price_usd=100.0,
        )
        with patch("backend.connectors.spot.resolve_spot") as rs:
            rs.return_value = SpotQuote(
                price=155.0,
                source="yahoo_chart",
                captured_at_utc=now,
                degraded=False,
            )
            out = vc.overlay_fresh_spot_on_roadmap(payload)
        self.assertTrue(out.slice_from_cache)
        self.assertEqual(out.current_price_usd, 155.0)

    def test_session_key_uses_last_completed_session(self):
        vc.store_slice_cache(vc.SLICE_SNAPSHOT, "AAPL", _minimal_snapshot())
        key = (vc.SLICE_SNAPSHOT, "AAPL", last_completed_session())
        self.assertIn(key, vc._store)

    def test_supabase_write_on_store(self):
        payload = _minimal_snapshot("NVDA")
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
                vc.store_slice_cache(vc.SLICE_SNAPSHOT, "NVDA", payload)
        self.assertEqual(upserted.get("ticker"), "NVDA")
        self.assertEqual(upserted.get("slice"), vc.SLICE_SNAPSHOT)


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
