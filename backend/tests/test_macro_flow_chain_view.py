"""Value chain payload from category snapshots."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.macro_flow.chain_view import build_value_chain_payload
from backend.macro_flow.db import init_macro_flow_db
from backend.macro_flow.seed import seed_macro_flow_db
from backend.macro_flow.store import persist_flow_snapshot

MOCK_CAPEX = {
    "available": True,
    "unit": "USD",
    "metric": "capex_ttm",
    "basis": "test basis",
    "source": "yfinance",
    "as_of": "2026-03-31",
    "years": ["2024", "2025"],
    "latest_label": "TTM reported CapEx",
    "stage_totals": [
        {"id": "retail_industry", "name": "Retail / Industry", "latest_usd": 50e9, "ticker_count": 3, "timeline": []},
        {"id": "hyperscaler", "name": "Hyperscaler", "latest_usd": 482e9, "ticker_count": 5, "timeline": []},
        {"id": "semiconductor", "name": "Semiconductor", "latest_usd": 28e9, "ticker_count": 4, "timeline": []},
        {"id": "foundry_infra", "name": "Foundry / Equipment", "latest_usd": 45e9, "ticker_count": 3, "timeline": []},
        {"id": "materials", "name": "Materials / Minerals", "latest_usd": 12e9, "ticker_count": 2, "timeline": []},
    ],
    "tickers": [],
}


class TestValueChainView(unittest.TestCase):
    def test_builds_ordered_stages_and_flows(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "chain_test.db")
            with patch.dict(os.environ, {"MACRO_FLOW_DB_PATH": db_path}):
                init_macro_flow_db()
                seed_macro_flow_db(db_path)
                persist_flow_snapshot(
                    interval="1w",
                    ts=1000.0,
                    flow_rows=[
                        {
                            "category_id": "ai_infra",
                            "cmf": 0.1,
                            "rs_ratio": 1.02,
                            "rs_momentum": 0.01,
                            "flow_score": 0.5,
                            "confidence": 0.6,
                            "top_movers": [],
                        },
                        {
                            "category_id": "cloud_software",
                            "cmf": 0.2,
                            "rs_ratio": 1.01,
                            "rs_momentum": 0.02,
                            "flow_score": 0.4,
                            "confidence": 0.6,
                            "top_movers": [],
                        },
                        {
                            "category_id": "consumer_health",
                            "cmf": 0.15,
                            "rs_ratio": 1.0,
                            "rs_momentum": 0.0,
                            "flow_score": 0.3,
                            "confidence": 0.5,
                            "top_movers": [],
                        },
                        {
                            "category_id": "energy_materials",
                            "cmf": 0.05,
                            "rs_ratio": 0.99,
                            "rs_momentum": -0.01,
                            "flow_score": 0.2,
                            "confidence": 0.5,
                            "top_movers": [],
                        },
                    ],
                    qual_rows=[],
                    entity_qual={},
                    qa_rows=[],
                    db_path=db_path,
                )

                async def _run():
                    with patch(
                        "backend.macro_flow.chain_view.fetch_stage_capex_payload",
                        return_value=MOCK_CAPEX,
                    ):
                        return await build_value_chain_payload("1w")

                payload = asyncio.run(_run())
        self.assertTrue(payload["has_data"])
        self.assertEqual(len(payload["stages"]), 5)
        self.assertEqual(payload["stages"][0]["name"], "Retail / Industry")
        self.assertEqual(payload["stages"][1]["name"], "Hyperscaler")
        self.assertEqual(len(payload["flows"]), 4)
        self.assertEqual(payload["flows"][0]["from_name"], "Retail / Industry")
        self.assertEqual(payload["flows"][0]["to_name"], "Hyperscaler")
        self.assertGreater(payload["flows"][0]["value"], 0)
        self.assertTrue(payload["spend"]["available"])
        self.assertEqual(payload["spend"]["metric"], "capex_ttm")
        self.assertGreater(payload["spend"]["stage_totals"][1]["latest_usd"], 100e9)


if __name__ == "__main__":
    unittest.main()
