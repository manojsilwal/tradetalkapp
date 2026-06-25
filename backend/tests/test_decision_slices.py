"""Offline tests for the three progressive decision-terminal slice runners.

These exercise run_decision_snapshot/verdict/roadmap_request with mocked
collaborators (no network, no LLM) and assert each slice returns the right
payload type and that the per-slice cache short-circuits a second call.
"""
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("GEMINI_PRIMARY", "0")

from backend import verdict_cache as vc  # noqa: E402
from backend.schemas import (  # noqa: E402
    DebateResult,
    DecisionRoadmapPayload,
    DecisionSnapshotPayload,
    DecisionVerdictPayload,
    MarketRegime,
    MarketState,
    SwarmConsensus,
)


def _swarm() -> SwarmConsensus:
    return SwarmConsensus(
        ticker="AAPL",
        macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
        global_signal=1,
        global_verdict="BUY",
        confidence=0.7,
        factors={},
    )


def _debate() -> DebateResult:
    return DebateResult(
        ticker="AAPL",
        arguments=[],
        verdict="BUY",
        consensus_confidence=0.8,
        moderator_summary="summary",
        bull_score=3,
        bear_score=1,
        neutral_score=1,
    )


class _ToolRegistry:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, name, args, timeout_s=None):
        self.calls += 1
        return {"ticker": "AAPL", "company_name": "Apple Inc", "current_price": 150.0}


class _PolyConnector:
    async def fetch_data(self, ticker):
        return {"events": [], "source": "test", "has_relevant_data": False}


class _SliceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        vc.clear_verdict_cache()
        os.environ["VERDICT_CACHE_ENABLE"] = "1"

    def tearDown(self) -> None:
        vc.clear_verdict_cache()
        os.environ.pop("VERDICT_CACHE_ENABLE", None)


class TestSnapshotRunner(_SliceTestBase):
    def test_returns_snapshot_and_caches(self) -> None:
        from backend.decision_terminal import run_decision_snapshot_request

        registry = _ToolRegistry()
        with patch(
            "backend.decision_terminal._sync_extended_snapshot", return_value={"trailingEps": 5.0}
        ), patch(
            "backend.decision_terminal._resolve_spot_for_terminal", return_value=None
        ), patch(
            "backend.decision_terminal._build_scorecard_for_terminal",
            new=_async_none,
        ), patch(
            "backend.decision_terminal._safe_momentum_fetch",
            new=_async_none,
        ):
            payload = asyncio.run(
                run_decision_snapshot_request("aapl", tool_registry=registry, force=True)
            )
            self.assertIsInstance(payload, DecisionSnapshotPayload)
            self.assertEqual(payload.ticker, "AAPL")
            self.assertIsNotNone(payload.valuation)
            self.assertIsNotNone(payload.quality)

            # Second (non-forced) call should hit the slice cache, not re-fetch.
            calls_before = registry.calls
            with patch.object(vc, "overlay_fresh_spot_on_snapshot", side_effect=lambda p: p):
                cached = asyncio.run(
                    run_decision_snapshot_request("aapl", tool_registry=registry, force=False)
                )
            self.assertIsInstance(cached, DecisionSnapshotPayload)
            self.assertEqual(registry.calls, calls_before)


class TestVerdictRunner(_SliceTestBase):
    def test_returns_verdict_with_embedded_swarm_debate(self) -> None:
        from backend.decision_terminal import run_decision_verdict_request

        analysis = SimpleNamespace(
            swarm=_swarm(), debate=_debate(), macro_fetched_at_utc="2026-06-25T00:00:00Z"
        )

        async def _execute_analyze(ticker, credit_stress, auth_user, **kwargs):
            task = kwargs.get("debate_data_task")
            if task is not None:
                await task
            return analysis

        registry = _ToolRegistry()
        with patch("backend.decision_terminal._emit_verdict_ledger") as emit:
            payload = asyncio.run(
                run_decision_verdict_request(
                    "aapl",
                    None,
                    None,
                    execute_analyze=_execute_analyze,
                    tool_registry=registry,
                    poly_connector=_PolyConnector(),
                    force=True,
                )
            )
        self.assertIsInstance(payload, DecisionVerdictPayload)
        self.assertEqual(payload.ticker, "AAPL")
        self.assertIsNotNone(payload.swarm)
        self.assertIsNotNone(payload.debate)
        self.assertEqual(payload.macro_fetched_at_utc, "2026-06-25T00:00:00Z")
        emit.assert_called_once()


class TestRoadmapRunner(_SliceTestBase):
    def test_returns_roadmap_heuristic_when_no_predictor(self) -> None:
        from backend.decision_terminal import run_decision_roadmap_request

        registry = _ToolRegistry()
        spot = SimpleNamespace(
            price=150.0,
            source="yahoo_chart",
            captured_at_utc="2026-06-25T00:00:00Z",
            degraded=False,
            momentum_anchor_usd=None,
        )
        # No predictor -> heuristic roadmap path (hist CAGR may be None -> unavailable).
        with patch(
            "backend.decision_terminal._resolve_spot_for_terminal", return_value=spot
        ), patch(
            "backend.brain.flags.brain_surface_enabled", return_value=False
        ), patch(
            "backend.predictor.agent.run_predictor_forecast",
            new=_async_predictor_fail,
        ), patch(
            "backend.decision_terminal._get_historical_cagr_3y", return_value=12.0
        ):
            payload = asyncio.run(
                run_decision_roadmap_request("aapl", tool_registry=registry, force=True)
            )
        self.assertIsInstance(payload, DecisionRoadmapPayload)
        self.assertEqual(payload.ticker, "AAPL")
        self.assertIsNotNone(payload.roadmap)
        self.assertTrue(payload.roadmap.used_heuristic_fallback)


async def _async_none(*args, **kwargs):
    return None


async def _async_predictor_fail(*args, **kwargs):
    return SimpleNamespace(status="error", base_price_usd_3y_scenario=None)


if __name__ == "__main__":
    unittest.main()
