import asyncio
import os
import unittest
from unittest.mock import patch


class TestPredictorGoldenPath(unittest.TestCase):
    def setUp(self) -> None:
        self._pe = os.environ.get("PREDICTOR_ENABLE")

    def tearDown(self) -> None:
        if self._pe is None:
            os.environ.pop("PREDICTOR_ENABLE", None)
        else:
            os.environ["PREDICTOR_ENABLE"] = self._pe

    @patch("backend.predictor.agent._load_price_series_from_data_lake")
    @patch("backend.predictor.agent.fetch_timesfm_forecast_http")
    def test_ok_path_monotonic_quantiles(self, mock_fetch, mock_load_lake) -> None:
        import numpy as np
        import math
        
        # 1. Mock the price series from data lake
        mock_load_lake.return_value = np.arange(100.0, 164.0, 1.0) # size 64

        # 2. Mock the TimesFM API service response
        mock_quantiles = []
        for i in range(64):
            mock_quantiles.append([
                math.log(170), # mean
                math.log(150), # Q10
                math.log(150),
                math.log(150),
                math.log(150),
                math.log(170), # Q50
                math.log(170),
                math.log(170),
                math.log(170),
                math.log(200), # Q90
            ])
        mock_fetch.return_value = {
            "quantiles": mock_quantiles,
            "model_version": "timesfm-2.5-mock",
        }

        os.environ["PREDICTOR_ENABLE"] = "1"
        os.environ["TIMESFM_SERVICE_URL"] = "http://localhost:5000"
        
        from backend.predictor.agent import run_predictor_forecast

        async def _run():
            return await run_predictor_forecast(
                "NVDA",
                horizons=["1d", "5d", "21d", "63d"],
                tool_registry=None,
                emit_ledger=False,
            )

        out = asyncio.run(_run())
        self.assertEqual(out.status, "ok")
        for b in out.horizon_bands_usd:
            self.assertIsNotNone(b.q10_usd)
            self.assertIsNotNone(b.q50_usd)
            self.assertIsNotNone(b.q90_usd)
            self.assertLessEqual(b.q10_usd, b.q50_usd)
            self.assertLessEqual(b.q50_usd, b.q90_usd)


if __name__ == "__main__":
    unittest.main()
