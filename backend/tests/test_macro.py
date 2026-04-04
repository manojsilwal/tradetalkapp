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


if __name__ == "__main__":
    unittest.main()
