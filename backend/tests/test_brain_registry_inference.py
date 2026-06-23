"""End-to-end: train -> register -> load -> infer the UI contract (offline)."""
import tempfile
import unittest

from backend.brain import DISCLAIMER, FEATURE_LIST, SIGNAL_GROUPS
from backend.brain import dataset, pipeline
from backend.brain.inference import InferenceEngine
from backend.brain.model_registry import ModelRegistry
from backend.brain.ports.local_adapters import LocalStorage


class TestRegistryAndInference(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.registry = ModelRegistry(root="artifacts", storage=LocalStorage(self.tmp))
        self.panel = dataset.synthetic_panel(n_tickers=70, n_periods=20, seed=3)

    def test_train_register_load_roundtrip(self):
        summ = pipeline.train_and_register(
            self.panel, version="v1", registry=self.registry,
            model_name="finrank-net", model_config={"epochs": 200, "hidden": 16},
        )
        self.assertTrue(self.registry.exists("finrank-net", "v1"))
        # validation AUC should beat random on the learnable synthetic signal
        self.assertGreater(summ["metrics"]["validation"]["auc"], 0.6)

        art = self.registry.load("finrank-net", "v1")
        self.assertEqual(art["feature_list"], list(FEATURE_LIST))
        self.assertEqual(art["model_version"], "v1")

    def test_corrupted_feature_list_detected(self):
        pipeline.train_and_register(self.panel, "v1", self.registry,
                                    model_name="logreg")
        # tamper with the stored feature list
        import json
        key = "artifacts/logreg-v1/feature_list.json"
        bad = json.dumps(["only_one_feature"]).encode()
        self.registry.storage.put(key, bad)
        with self.assertRaises(ValueError):
            self.registry.load("logreg", "v1")

    def test_inference_contract_shape(self):
        pipeline.train_and_register(self.panel, "v1", self.registry,
                                    model_name="finrank-net",
                                    model_config={"epochs": 150})
        eng = InferenceEngine(self.registry, "finrank-net", "v1")
        rows = self.panel["rows"][:70]
        tickers = self.panel["tickers"][:70]
        ranked = eng.rank_universe(rows, tickers, as_of_date="2026-06-22")

        self.assertEqual(len(ranked), 70)
        # sorted by probability descending
        probs = [c["outperform_probability"] for c in ranked]
        self.assertEqual(probs, sorted(probs, reverse=True))

        c = ranked[0]
        # Non-negotiable: model_version + as_of_date + disclaimer always present.
        self.assertEqual(c["model_version"], "v1")
        self.assertEqual(c["as_of_date"], "2026-06-22")
        self.assertEqual(c["disclaimer"], DISCLAIMER)
        self.assertEqual(c["horizon_days"], 63)
        self.assertTrue(0.0 <= c["outperform_probability"] <= 1.0)
        self.assertTrue(0.0 <= c["confidence_score"] <= 1.0)
        self.assertIn(c["recommendation"], ("constructive", "neutral", "cautious"))
        for g in SIGNAL_GROUPS:
            self.assertIn(g, c["signal_scores"])
        self.assertIn("supporting", c["drivers"])
        self.assertIn("detracting", c["drivers"])

    def test_predict_single_ticker_without_peers(self):
        pipeline.train_and_register(self.panel, "v1", self.registry, model_name="logreg")
        eng = InferenceEngine(self.registry, "logreg", "v1")
        c = eng.predict_ticker(self.panel["rows"][0], "AAPL", "2026-06-22")
        self.assertEqual(c["ticker"], "AAPL")
        self.assertIsNotNone(c["composite_score"])


if __name__ == "__main__":
    unittest.main()
