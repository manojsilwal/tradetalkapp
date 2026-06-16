"""Live data orchestrator — offline tests with mocked providers."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.connectors.live_data_orchestrator import (
    LiveDataBundle,
    apply_bundle_enrichment,
    apply_quotes_to_row,
    fetch_live_bundle,
)
from backend.connectors.yfinance_capability import reset_all_for_tests


class TestLiveDataOrchestrator(unittest.TestCase):
    def setUp(self):
        reset_all_for_tests()

    def tearDown(self):
        reset_all_for_tests()

    def test_parallel_bundle_merges_quotes_and_fundamentals(self):
        async def _run():
            with patch(
                "backend.connectors.live_data_orchestrator._fetch_yfinance_prices",
                new=AsyncMock(return_value=({"AAPL": {"price": 190.0, "pct": 1.2}}, False)),
            ), patch(
                "backend.connectors.live_data_orchestrator._fetch_fundamentals_many",
                new=AsyncMock(
                    return_value={
                        "AAPL": {
                            "market_cap": 3e12,
                            "pe_ratio": 28.5,
                            "source": "fincrawler",
                        }
                    }
                ),
            ), patch(
                "backend.connectors.live_data_orchestrator._fetch_news_many",
                new=AsyncMock(return_value={}),
            ), patch(
                "backend.connectors.live_data_orchestrator._fetch_sec",
                new=AsyncMock(return_value={}),
            ):
                return await fetch_live_bundle(
                    ["AAPL"],
                    want=("price", "fundamentals"),
                    deadline_s=2.0,
                    force=True,
                )

        bundle = asyncio.run(_run())
        self.assertIn("AAPL", bundle.quotes)
        self.assertIn("AAPL", bundle.fundamentals)
        self.assertEqual(bundle.sources.get("price"), "yfinance")
        self.assertEqual(bundle.sources.get("fundamentals"), "fincrawler")

    def test_fincrawler_price_when_yfinance_fails(self):
        async def _run():
            with patch(
                "backend.connectors.live_data_orchestrator._fetch_yfinance_prices",
                new=AsyncMock(return_value=({}, True)),
            ), patch(
                "backend.connectors.live_data_orchestrator._fetch_fincrawler_prices",
                new=AsyncMock(return_value={"MSFT": {"price": 420.0, "pct": None}}),
            ), patch(
                "backend.connectors.live_data_orchestrator._fetch_fundamentals_many",
                new=AsyncMock(return_value={}),
            ):
                return await fetch_live_bundle(["MSFT"], want=("price",), force=True)

        bundle = asyncio.run(_run())
        self.assertEqual(bundle.quotes["MSFT"]["price"], 420.0)
        self.assertEqual(bundle.sources.get("price"), "fincrawler")

    def test_apply_bundle_enrichment_sets_row_fields(self):
        row = {"symbol": "NVDA"}
        bundle = LiveDataBundle(
            fundamentals={
                "NVDA": {
                    "market_cap": 2e12,
                    "pe_ratio": 55.0,
                    "source": "fincrawler",
                }
            },
            news={
                "NVDA": [{"title": "NVIDIA beats estimates", "summary": "Revenue up"}]
            },
        )
        apply_bundle_enrichment(row, bundle)
        self.assertEqual(row["pe_ratio"], 55.0)
        self.assertEqual(row["enrichment_source"], "fincrawler")
        self.assertEqual(row["primary_cause_headline"], "NVIDIA beats estimates")

    def test_apply_quotes_to_row(self):
        row = {"symbol": "AAPL"}
        ok = apply_quotes_to_row(row, {"AAPL": {"price": 191.0, "pct": 0.5, "previous_close": 190.0}})
        self.assertTrue(ok)
        self.assertEqual(row["close"], 191.0)
        self.assertEqual(row["daily_return_pct"], 0.5)


if __name__ == "__main__":
    unittest.main()
