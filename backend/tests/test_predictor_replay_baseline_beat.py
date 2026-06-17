"""Replay corpus smoke + historical calibration gate."""

import asyncio
import json
import os
import unittest
from unittest import mock

from backend.predictor.eval.historical_calibration import run_historical_calibration


class TestReplayBaselineBeat(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["TIMESFM_SERVICE_URL"] = "http://localhost:5000"
        os.environ["PREDICTOR_ENABLE"] = "1"
        os.environ["PREDICTOR_USE_DATA_LAKE"] = "1"

    def tearDown(self) -> None:
        os.environ.pop("TIMESFM_SERVICE_URL", None)
        os.environ.pop("PREDICTOR_ENABLE", None)
        os.environ.pop("PREDICTOR_USE_DATA_LAKE", None)

    @mock.patch("backend.connectors.macro.MacroHealthConnector.fetch_data")
    @mock.patch("backend.predictor.agent._load_price_series_from_data_lake")
    @mock.patch("backend.predictor.agent.fetch_timesfm_forecast_http")
    def test_mock_forecast_produces_finite_paths(self, mock_fetch, mock_load_lake, mock_macro_fetch) -> None:
        """Ensures end-to-end mock path yields usable USD levels for corpus tickers."""
        import numpy as np
        import math

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

        corpus_path = os.path.join(
            os.path.dirname(__file__), "..", "predictor", "replay_corpus.json"
        )
        with open(os.path.abspath(corpus_path), encoding="utf-8") as fh:
            rows = json.load(fh)
        sample = rows[:3]

        from backend.predictor.agent import run_predictor_forecast

        async def _one(t: str):
            return await run_predictor_forecast(
                t,
                horizons=["5d"],
                tool_registry=None,
                emit_ledger=False,
            )

        for row in sample:
            out = asyncio.run(_one(row["ticker"]))
            self.assertEqual(out.status, "ok")
            self.assertTrue(out.horizon_bands_usd)

    def test_historical_calibration_gate(self) -> None:
        os.environ["PREDICTOR_ENABLE"] = "1"
        os.environ["PREDICTOR_USE_DATA_LAKE"] = "0"
        out = run_historical_calibration(limit=30)
        self.assertTrue(out.get("ok"), msg=str(out))
        self.assertGreaterEqual(out.get("evaluated", 0), 10)


if __name__ == "__main__":
    unittest.main()
