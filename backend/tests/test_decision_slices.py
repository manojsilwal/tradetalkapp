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
    DecisionSwarmPayload,
    DecisionVerdictPayload,
    MarketRegime,
    MarketState,
    OptionsFlow,
    SwarmConsensus,
)


def _swarm(options: OptionsFlow | None = None) -> SwarmConsensus:
    return SwarmConsensus(
        ticker="AAPL",
        macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
        global_signal=1,
        global_verdict="BUY",
        confidence=0.7,
        factors={},
        options=options,
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
        from backend.market_bundle import MarketContext, FundamentalsBundle
        from backend.connectors.spot import SpotQuote

        registry = _ToolRegistry()
        debate = {"ticker": "AAPL", "company_name": "Apple Inc", "current_price": 150.0}
        ext = {"trailingEps": 5.0}
        ctx = MarketContext(
            ticker="AAPL",
            spot=SpotQuote(
                price=150.0,
                source="test",
                captured_at_utc="2026-06-25T00:00:00Z",
                degraded=False,
            ),
            debate_data=debate,
            valuation_ext=ext,
            fundamentals=FundamentalsBundle(
                ticker="AAPL", debate_data=debate, valuation_ext=ext
            ),
            as_of_utc="2026-06-25T00:00:00Z",
        )

        async def _mock_ctx(*args, **kwargs):
            registry.calls += 1
            return ctx

        with patch(
            "backend.market_bundle.fetch_market_context",
            side_effect=_mock_ctx,
        ), patch(
            "backend.decision_terminal._build_scorecard_for_terminal",
            new=_async_none,
        ), patch(
            "backend.decision_terminal._safe_momentum_fetch",
            new=_async_none,
        ), patch(
            "backend.decision_terminal._safe_analyst_targets_fetch",
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

        async def _execute_swarm_trace(ticker, credit_stress, auth_user):
            return _swarm(), {"indicators": {"fred_fetched_at": "2026-06-25T00:00:00Z"}}

        async def _execute_debate(ticker, auth_user, **kwargs):
            task = kwargs.get("market_context_task") or kwargs.get("debate_data_task")
            if task is not None:
                await task
            return _debate()

        registry = _ToolRegistry()
        with patch("backend.decision_terminal._emit_verdict_ledger") as emit:
            payload = asyncio.run(
                run_decision_verdict_request(
                    "aapl",
                    None,
                    None,
                    execute_analyze=_execute_analyze_unused,
                    execute_swarm_trace=_execute_swarm_trace,
                    execute_debate=_execute_debate,
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


class TestSwarmRunner(_SliceTestBase):
    def test_returns_swarm_and_caches(self) -> None:
        from backend.decision_terminal import run_decision_swarm_request

        async def _execute_swarm_trace(ticker, credit_stress, auth_user):
            return _swarm(), {"indicators": {"fred_fetched_at": "2026-06-25T00:00:00Z"}}

        with patch("backend.decision_terminal._safe_poly_fetch", new=_async_poly_empty):
            payload = asyncio.run(
                run_decision_swarm_request(
                    "aapl",
                    None,
                    None,
                    execute_swarm_trace=_execute_swarm_trace,
                    poly_connector=_PolyConnector(),
                    force=True,
                )
            )
        self.assertIsInstance(payload, DecisionSwarmPayload)
        self.assertEqual(payload.ticker, "AAPL")
        self.assertIsNotNone(payload.swarm)
        self.assertEqual(payload.verdict.swarm_verdict, "BUY")

        with patch("backend.decision_terminal._safe_poly_fetch", new=_async_poly_empty):
            cached = asyncio.run(
                run_decision_swarm_request(
                    "aapl",
                    None,
                    None,
                    execute_swarm_trace=_execute_swarm_trace,
                    poly_connector=_PolyConnector(),
                    force=False,
                )
            )
        self.assertTrue(cached.slice_from_cache)


class TestDebateRunner(_SliceTestBase):
    def test_reuses_cached_swarm_context(self) -> None:
        from backend.decision_terminal import (
            run_decision_debate_request,
            run_decision_swarm_request,
        )

        swarm_calls = {"n": 0}
        debate_contexts: list[str] = []

        async def _execute_swarm_trace(ticker, credit_stress, auth_user):
            swarm_calls["n"] += 1
            return _swarm(), {"indicators": {"fred_fetched_at": "2026-06-25T00:00:00Z"}}

        async def _execute_debate(ticker, auth_user, **kwargs):
            debate_contexts.append(kwargs.get("swarm_context") or "")
            task = kwargs.get("market_context_task") or kwargs.get("debate_data_task")
            if task is not None:
                await task
            return _debate()

        registry = _ToolRegistry()
        with patch("backend.decision_terminal._safe_poly_fetch", new=_async_poly_empty), patch(
            "backend.decision_terminal._emit_verdict_ledger"
        ):
            asyncio.run(
                run_decision_swarm_request(
                    "aapl",
                    None,
                    None,
                    execute_swarm_trace=_execute_swarm_trace,
                    poly_connector=_PolyConnector(),
                    force=True,
                )
            )
            payload = asyncio.run(
                run_decision_debate_request(
                    "aapl",
                    None,
                    None,
                    execute_debate=_execute_debate,
                    execute_swarm_trace=_execute_swarm_trace,
                    tool_registry=registry,
                    poly_connector=_PolyConnector(),
                    force=False,
                )
            )
        self.assertIsInstance(payload, DecisionVerdictPayload)
        self.assertEqual(swarm_calls["n"], 1)
        self.assertTrue(any("Swarm pre-analysis for AAPL" in ctx for ctx in debate_contexts))


class TestRoadmapRunner(_SliceTestBase):
    def test_returns_roadmap_heuristic_when_no_predictor(self) -> None:
        from backend.decision_terminal import run_decision_roadmap_request
        from backend.market_bundle import MarketContext, FundamentalsBundle
        from backend.connectors.spot import SpotQuote

        registry = _ToolRegistry()
        debate = {"ticker": "AAPL", "company_name": "Apple Inc", "current_price": 150.0}
        ctx = MarketContext(
            ticker="AAPL",
            spot=SpotQuote(
                price=150.0,
                source="yahoo_chart",
                captured_at_utc="2026-06-25T00:00:00Z",
                degraded=False,
            ),
            debate_data=debate,
            valuation_ext={},
            fundamentals=FundamentalsBundle(
                ticker="AAPL", debate_data=debate, valuation_ext={}
            ),
            as_of_utc="2026-06-25T00:00:00Z",
        )
        # No predictor -> heuristic roadmap path (hist CAGR may be None -> unavailable).
        with patch(
            "backend.market_bundle.fetch_market_context",
            new=_async_market_ctx_factory(ctx),
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


def _async_market_ctx_factory(ctx):
    async def _fn(*args, **kwargs):
        return ctx

    return _fn


async def _async_predictor_fail(*args, **kwargs):
    return SimpleNamespace(status="error", base_price_usd_3y_scenario=None)


async def _async_poly_empty(*args, **kwargs):
    return {"events": [], "source": "test", "has_relevant_data": False}


async def _execute_analyze_unused(*args, **kwargs):
    raise AssertionError("execute_analyze should not be called in split verdict path")


if __name__ == "__main__":
    unittest.main()
