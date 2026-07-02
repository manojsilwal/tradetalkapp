"""Cross-surface market data parity (offline, mocked)."""
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.connectors.spot import SpotQuote
from backend.market_bundle import FundamentalsBundle, MarketContext, fetch_market_context


def _ctx(ticker: str = "AAPL", price: float = 150.0) -> MarketContext:
    debate = {
        "ticker": ticker,
        "company_name": "Apple Inc",
        "current_price": price,
        "pe_ratio": 28.0,
        "roe": 150.0,
    }
    ext = {"trailingEps": 5.0, "returnOnEquity": 1.5}
    spot = SpotQuote(
        price=price,
        source="yahoo_chart",
        captured_at_utc="2026-07-01T00:00:00Z",
        degraded=False,
    )
    return MarketContext(
        ticker=ticker,
        spot=spot,
        debate_data=debate,
        valuation_ext=ext,
        fundamentals=FundamentalsBundle(
            ticker=ticker, debate_data=debate, valuation_ext=ext
        ),
        as_of_utc="2026-07-01T00:00:00Z",
    )


class TestMarketContextParity(unittest.TestCase):
    def test_snapshot_and_roadmap_share_spot(self) -> None:
        from backend.decision_terminal import (
            build_snapshot_slice,
            run_decision_roadmap_request,
        )

        ctx = _ctx("AAPL", 150.0)

        async def _run():
            with patch(
                "backend.market_bundle.fetch_market_context",
                new=AsyncMock(return_value=ctx),
            ), patch(
                "backend.decision_terminal._build_scorecard_for_terminal",
                new=AsyncMock(return_value=None),
            ), patch(
                "backend.decision_terminal._safe_momentum_fetch",
                new=AsyncMock(return_value=None),
            ), patch(
                "backend.decision_terminal._safe_analyst_targets_fetch",
                new=AsyncMock(return_value={}),
            ), patch(
                "backend.brain.flags.brain_surface_enabled",
                return_value=False,
            ), patch(
                "backend.predictor.agent.run_predictor_forecast",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        status="error", base_price_usd_3y_scenario=None
                    )
                ),
            ), patch(
                "backend.decision_terminal._get_historical_cagr_3y",
                return_value=10.0,
            ):
                snap = build_snapshot_slice(
                    "AAPL",
                    ctx.debate_data,
                    ctx.valuation_ext,
                    spot_quote=ctx.spot,
                )
                road = await run_decision_roadmap_request(
                    "AAPL", tool_registry=SimpleNamespace(), force=True
                )
                return snap, road

        snap, road = asyncio.run(_run())
        self.assertEqual(snap.valuation.current_price_usd, 150.0)
        self.assertEqual(road.current_price_usd, 150.0)

    def test_debate_receives_market_context_price(self) -> None:
        from backend.debate_agents import run_full_debate
        from backend.routers.analysis import _execute_debate

        ctx = _ctx("NVDA", 198.0)

        async def _return_ctx():
            return ctx

        async def _run():
            task = asyncio.create_task(_return_ctx())
            with patch(
                "backend.debate_agents.run_full_debate", new=AsyncMock()
            ) as mock_debate, patch(
                "backend.brain.cutover.aserve_for_surface", new=AsyncMock(return_value=None)
            ), patch(
                "backend.routers.analysis.knowledge_store"
            ):
                mock_debate.return_value = SimpleNamespace(
                    ticker="NVDA",
                    verdict="BUY",
                    consensus_confidence=0.8,
                    arguments=[],
                    moderator_summary="",
                    bull_score=1,
                    bear_score=0,
                )
                await _execute_debate(
                    "NVDA",
                    None,
                    market_context_task=task,
                    macro_data={
                        "indicators": {
                            "credit_stress_index": 0.5,
                            "vix_level": 15.0,
                        }
                    },
                )
                return mock_debate

        mock = asyncio.run(_run())
        debate_data = mock.call_args[0][1]
        self.assertEqual(debate_data["current_price"], 198.0)


if __name__ == "__main__":
    unittest.main()
