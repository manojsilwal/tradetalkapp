"""CapEx fetch for value-chain spend."""
import asyncio
import unittest
from unittest.mock import patch

from backend.macro_flow.capex_data import (
    STAGE_TICKERS,
    _abs_capex,
    _fx_to_usd,
    _pick_capex_series,
    build_flows_from_stage_capex,
    fetch_stage_capex_payload,
)


class TestCapexData(unittest.TestCase):
    def test_abs_capex_negative(self):
        self.assertEqual(_abs_capex(-1_500_000_000), 1_500_000_000.0)

    def test_pick_capex_series_prefers_capital_expenditure(self):
        import pandas as pd

        df = pd.DataFrame(
            {
                "2024": [-100.0],
                "2023": [-80.0],
            },
            index=["Capital Expenditure", "Free Cash Flow"],
        )
        series = _pick_capex_series(df)
        self.assertIsNotNone(series)
        self.assertEqual(float(series["2024"]), -100.0)

    def test_build_flows_uses_target_stage_capex(self):
        totals = [
            {"id": "hyperscaler", "name": "Hyperscaler", "latest_usd": 400e9, "timeline": []},
            {"id": "semiconductor", "name": "Semiconductor", "latest_usd": 30e9, "timeline": []},
        ]
        flows = build_flows_from_stage_capex(
            totals,
            (("hyperscaler", "semiconductor", "GPU orders"),),
        )
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["latest_usd"], 30e9)

    def test_fetch_stage_capex_mocked(self):
        mock_payload = {
            "available": True,
            "unit": "USD",
            "metric": "capex_ttm",
            "source": "yfinance",
            "as_of": "2026-03-31",
            "years": ["2024", "2025"],
            "latest_label": "TTM reported CapEx",
            "stage_totals": [
                {
                    "id": "hyperscaler",
                    "name": "Hyperscaler",
                    "latest_usd": 482e9,
                    "ticker_count": 5,
                    "timeline": [{"year": "2025", "usd": 400e9}],
                }
            ],
            "tickers": [],
        }

        async def _run():
            with patch(
                "backend.macro_flow.capex_data._fetch_stage_capex_sync",
                return_value=mock_payload,
            ):
                return await fetch_stage_capex_payload()

        out = asyncio.run(_run())
        self.assertTrue(out["available"])
        self.assertEqual(out["stage_totals"][0]["latest_usd"], 482e9)

    def test_stage_tickers_no_double_amzn(self):
        retail = STAGE_TICKERS["retail_industry"]
        hyperscaler = STAGE_TICKERS["hyperscaler"]
        self.assertNotIn("AMZN", retail)
        self.assertIn("AMZN", hyperscaler)


if __name__ == "__main__":
    unittest.main()
