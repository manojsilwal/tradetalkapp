"""Replay corpus smoke + historical calibration gate."""

import asyncio
import json
import os
import unittest

from backend.predictor.eval.historical_calibration import run_historical_calibration


class TestReplayBaselineBeat(unittest.TestCase):
    def test_mock_forecast_produces_finite_paths(self) -> None:
        """Ensures end-to-end mock path yields usable USD levels for corpus tickers."""
        corpus_path = os.path.join(
            os.path.dirname(__file__), "..", "predictor", "replay_corpus.json"
        )
        with open(os.path.abspath(corpus_path), encoding="utf-8") as fh:
            rows = json.load(fh)
        sample = rows[:3]
        os.environ["PREDICTOR_ENABLE"] = "1"
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
