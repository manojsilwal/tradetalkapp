import unittest
from unittest import mock
import os
import asyncio

from backend.predictor.eval.runner import run_replay_smoke


class TestEvalSmoke(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["TIMESFM_SERVICE_URL"] = "http://localhost:5000"
        os.environ["PREDICTOR_ENABLE"] = "1"

    def tearDown(self) -> None:
        os.environ.pop("TIMESFM_SERVICE_URL", None)
        os.environ.pop("PREDICTOR_ENABLE", None)

    @mock.patch("backend.connectors.macro.MacroHealthConnector.fetch_data")
    @mock.patch("backend.predictor.agent._load_price_series_from_data_lake")
    @mock.patch("backend.predictor.agent.fetch_timesfm_forecast_http")
    def test_replay_runs(self, mock_fetch, mock_load_lake, mock_macro_fetch) -> None:
        import numpy as np
        import math

        # Mappings as identified by the test runner:
        # mock_macro_fetch -> MacroHealthConnector.fetch_data
        # mock_fetch -> fetch_timesfm_forecast_http
        # mock_load_lake -> _load_price_series_from_data_lake

        mock_macro_fetch.return_value = {
            "status": "Normal",
            "indicators": {"credit_stress_index": 1.0, "vix_level": 15.0},
        }
        mock_load_lake.return_value = np.arange(100.0, 164.0, 1.0)
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

        out = run_replay_smoke(limit=3)
        self.assertTrue(out.get("ok"), msg=str(out))
        self.assertGreaterEqual(out.get("ok_count", 0), 1)


if __name__ == "__main__":
    unittest.main()
