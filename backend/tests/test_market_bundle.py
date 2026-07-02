"""Offline tests for request-scoped MarketContext / FundamentalsBundle."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.connectors.spot import SpotQuote
from backend.market_bundle import (
    FundamentalsBundle,
    MarketContext,
    apply_spot_to_debate,
    build_fundamentals_merged,
    fetch_market_context,
)


class TestFundamentalsBundle(unittest.TestCase):
    def test_merge_prefers_debate_for_overlapping_keys(self) -> None:
        debate = {"ticker": "NVDA", "pe_ratio": 45.0, "current_price": 198.0}
        ext = {"ticker": "NVDA", "trailingEps": 2.5, "regularMarketPrice": 190.0}
        merged = build_fundamentals_merged(debate, ext)
        self.assertEqual(merged["pe_ratio"], 45.0)
        self.assertEqual(merged["trailingEps"], 2.5)
        self.assertEqual(merged["current_price"], 198.0)

    def test_apply_spot_overlays_debate_price(self) -> None:
        spot = SpotQuote(
            price=200.5,
            source="yahoo_chart",
            captured_at_utc="2026-07-01T00:00:00Z",
            degraded=False,
            momentum_anchor_usd=199.0,
        )
        out = apply_spot_to_debate({"current_price": 150.0}, spot)
        self.assertEqual(out["current_price"], 200.5)
        self.assertEqual(out["spot_price_source"], "yahoo_chart")
        self.assertEqual(out["momentum_anchor_price"], 199.0)


class TestFetchMarketContext(unittest.TestCase):
    def test_fetch_builds_context(self) -> None:
        debate = {
            "ticker": "AAPL",
            "company_name": "Apple Inc",
            "current_price": 150.0,
            "momentum_anchor_price": 149.5,
        }
        ext = {"trailingEps": 6.5}
        spot = SpotQuote(
            price=151.0,
            source="yahoo_chart",
            captured_at_utc="2026-07-01T00:00:00Z",
            degraded=False,
        )

        async def _run() -> MarketContext:
            with patch(
                "backend.market_bundle.fetch_yfinance_valuation_snapshot",
                return_value=ext,
            ), patch(
                "backend.connectors.debate_data.fetch_debate_data",
                new=AsyncMock(return_value=debate),
            ), patch(
                "backend.market_bundle.resolve_spot",
                return_value=spot,
            ):
                return await fetch_market_context("AAPL")

        ctx = asyncio.run(_run())
        self.assertEqual(ctx.ticker, "AAPL")
        self.assertEqual(ctx.debate_data["current_price"], 151.0)
        self.assertEqual(ctx.valuation_ext["trailingEps"], 6.5)
        self.assertIsInstance(ctx.fundamentals, FundamentalsBundle)


if __name__ == "__main__":
    unittest.main()
