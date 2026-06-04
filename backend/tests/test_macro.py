import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.main import app


class TestMacroRoute(unittest.TestCase):
    @patch("backend.routers.macro.macro_connector.fetch_data", new_callable=AsyncMock)
    def test_macro_response_includes_extended_indicator_fields(self, mock_fetch):
        mock_fetch.return_value = {
            "indicators": {
                "vix_level": 18.2,
                "credit_stress_index": 0.94,
                "usd_broad_index": 123.4,
                "usd_index_change_5d_pct": 0.9,
                "usd_strength_label": "strong",
                "dxy_level": 104.1,
                "dxy_change_5d_pct": 0.4,
                "dxy_strength_label": "firm",
                "treasury_2y": 4.1,
                "treasury_10y": 4.45,
                "yield_curve_spread_10y_2y": 0.35,
                "fed_funds_rate": 5.25,
                "cpi_yoy": 3.1,
                "unemployment": 4.0,
                "macro_narrative": "Rates elevated, dollar firm.",
                "fred_fetched_at": "2026-04-04T00:00:00+00:00",
            },
            "sectors": [],
            "consumer_spending": [],
            "capital_flows": [],
            "cash_reserves": [],
        }

        with TestClient(app) as client:
            response = client.get("/macro")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["dxy_level"], 104.1)
        self.assertEqual(payload["treasury_10y"], 4.45)
        self.assertEqual(payload["macro_narrative"], "Rates elevated, dollar firm.")
        self.assertEqual(payload["fred_fetched_at"], "2026-04-04T00:00:00+00:00")


class TestMacroFlowRoutes(unittest.TestCase):
    def test_macro_flow_categories_after_seed(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "macro_flow_test.db")
            with patch.dict(os.environ, {"MACRO_FLOW_DB_PATH": db_path}):
                from backend.macro_flow.db import init_macro_flow_db
                from backend.macro_flow.seed import seed_macro_flow_db

                init_macro_flow_db()
                seed_macro_flow_db(db_path)
                with TestClient(app) as client:
                    response = client.get("/macro/flow/categories")
        self.assertEqual(response.status_code, 200, response.text)
        cats = response.json().get("categories") or []
        self.assertGreaterEqual(len(cats), 5)


class TestMacroFlowCronRefresh(unittest.TestCase):
    @patch("backend.macro_flow.orchestrator.run_macro_flow_pipeline", new_callable=AsyncMock)
    def test_cron_refresh_ok_with_secret(self, mock_pipe):
        mock_pipe.return_value = {"interval": "1w", "timestamp": 1.0, "categories": 6, "edges": 5}
        with patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "cron-test"}):
            with TestClient(app) as client:
                bad = client.post("/macro/flow/cron-refresh?interval=1w")
                self.assertEqual(bad.status_code, 401)
                ok = client.post(
                    "/macro/flow/cron-refresh?interval=1w",
                    headers={"Authorization": "Bearer cron-test"},
                )
                self.assertEqual(ok.status_code, 200, ok.text)
                self.assertTrue(ok.json().get("ok"))
        mock_pipe.assert_called_once()



class TestMacroGlobalMarkets(unittest.TestCase):
    @patch("yfinance.download")
    def test_get_global_markets_success(self, mock_download):
        import pandas as pd
        # Create a mock DataFrame
        dates = pd.date_range(start="2026-05-01", periods=3, freq="D")
        columns = pd.MultiIndex.from_tuples([
            ("Close", "SPY"),
            ("Close", "TLT")
        ])
        data = [
            [100.0, 50.0],
            [105.0, 49.0],
            [102.0, 51.0]
        ]
        mock_df = pd.DataFrame(data, index=dates, columns=columns)
        mock_download.return_value = mock_df

        with TestClient(app) as client:
            response = client.get("/macro/global-markets?period=3M&tickers=SPY,TLT")
        
        self.assertEqual(response.status_code, 200)
        res = response.json()
        self.assertIn("dates", res)
        self.assertIn("series", res)
        self.assertEqual(res["dates"], ["2026-05-01", "2026-05-02", "2026-05-03"])
        self.assertEqual(res["series"]["SPY"], [0.0, 5.0, 2.0])
        self.assertEqual(res["series"]["TLT"], [0.0, -2.0, 2.0])


if __name__ == "__main__":
    unittest.main()

