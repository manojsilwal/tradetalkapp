import asyncio
import os
import tempfile
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from unittest import mock

from backend import decision_ledger as dl  # noqa: E402


class TestPredictorLedgerEmit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "d.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        os.environ["TIMESFM_SERVICE_URL"] = "http://localhost:5000"
        dl._reset_singleton_for_tests()

    def tearDown(self) -> None:
        dl._reset_singleton_for_tests()
        os.environ.pop("DECISIONS_DB_PATH", None)
        os.environ.pop("TIMESFM_SERVICE_URL", None)

    @mock.patch("backend.predictor.agent._load_price_series_from_data_lake")
    @mock.patch("backend.predictor.agent.fetch_timesfm_forecast_http")
    def test_emits_price_forecast_rows(self, mock_fetch, mock_load_lake) -> None:
        import numpy as np
        import math
        from backend.predictor.agent import run_predictor_forecast

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

        async def _run():
            return await run_predictor_forecast(
                "AAPL",
                horizons=["1d", "63d"],
                tool_registry=None,
                emit_ledger=True,
            )

        os.environ["PREDICTOR_ENABLE"] = "1"
        out = asyncio.run(_run())
        self.assertEqual(out.status, "ok")
        rows = dl.get_ledger().list_decisions_since(0.0, decision_type="price_forecast")
        self.assertGreaterEqual(len(rows), 1)
        # Quantile band must thread into output_json so the outcome grader can
        # score forecast_band_hit / forecast_pinball at T+H.
        for ev in rows:
            self.assertIn("q10_usd", ev.output)
            self.assertIn("q50_usd", ev.output)
            self.assertIn("q90_usd", ev.output)


if __name__ == "__main__":
    unittest.main()
