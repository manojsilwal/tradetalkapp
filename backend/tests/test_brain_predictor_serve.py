"""Brain predictor serving from snapshot TimesFM bands (offline)."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.brain.ports.local_adapters import LocalStorage
from backend.brain.predictor_serve import run_brain_predictor_forecast
from backend.brain.snapshot_store import BrainSnapshot, SnapshotStore


class TestBrainPredictorServe(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"STORAGE_BACKEND": "local", "BRAIN_STORAGE_ROOT": self._tmpdir.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmpdir.cleanup()

    def _seed_snapshot(self, ticker: str = "AAPL", with_bands: bool = True):
        storage = LocalStorage(root=self._tmpdir.name)
        store = SnapshotStore(storage=storage)
        bands = (
            [
                {"horizon": "1d", "q10": 98.0, "q50": 100.0, "q90": 102.0},
                {"horizon": "5d", "q10": 95.0, "q50": 101.0, "q90": 108.0},
                {"horizon": "21d", "q10": 90.0, "q50": 105.0, "q90": 120.0},
                {"horizon": "63d", "q10": 85.0, "q50": 110.0, "q90": 135.0},
            ]
            if with_bands
            else []
        )
        contract = {
            "ticker": ticker,
            "as_of_date": "2026-06-24",
            "model_name": "finrank-net",
            "model_version": "v1",
            "horizon_days": 63,
            "outperform_probability": 0.6,
            "recommendation": "constructive",
            "composite_score": 55.0,
            "signal_scores": {},
            "risk_score": 0.4,
            "confidence_score": 0.5,
            "data_completeness": 0.5,
            "drivers": {"supporting": [], "detracting": []},
            "disclaimer": "test",
        }
        snap = BrainSnapshot(
            ticker=ticker,
            as_of_date="2026-06-24",
            computed_at="2026-06-24T00:00:00Z",
            model_name="finrank-net",
            model_version="v1",
            horizon_days=63,
            base_contract=contract,
            base_feature_row={"ticker": ticker},
            base_price=100.0,
            price_tail=[100.0] * 64,
            timesfm_bands=bands,
            timesfm_model_version="timesfm-v1",
        )
        store.save(snap)
        status_path = "brain/status.json"
        storage.put(status_path, json.dumps({"as_of_date": "2026-06-24"}).encode())
        with patch("backend.brain.run_brain_pipeline.read_status") as rs:
            rs.return_value = {"as_of_date": "2026-06-24"}
            with patch("backend.brain.serving._live_price", return_value=(100.0, "test_spot")):
                return run_brain_predictor_forecast(ticker, ["1d", "63d"])

    def test_serves_horizon_bands_from_snapshot(self):
        resp = self._seed_snapshot()
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.ticker, "AAPL")
        self.assertEqual(len(resp.horizon_bands_usd), 2)
        self.assertIsNotNone(resp.base_price_usd_3y_scenario)
        self.assertEqual(resp.meta.get("source"), "brain_snapshot")

    def test_missing_bands_returns_insufficient_data(self):
        resp = self._seed_snapshot(with_bands=False)
        self.assertEqual(resp.status, "insufficient_data")

    def test_predictor_flag_gates_analysis_router(self):
        from backend.brain.flags import brain_surface_enabled

        for k in ("BRAIN_SERVE_ENABLE", "BRAIN_CUTOVER_PREDICTOR"):
            os.environ.pop(k, None)
        self.assertFalse(brain_surface_enabled("predictor"))
        os.environ["BRAIN_SERVE_ENABLE"] = "1"
        os.environ["BRAIN_CUTOVER_PREDICTOR"] = "1"
        self.assertTrue(brain_surface_enabled("predictor"))


if __name__ == "__main__":
    unittest.main()
