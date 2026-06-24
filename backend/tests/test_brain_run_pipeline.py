"""Nightly brain pipeline + serving + ledger, fully offline.

BigQuery and the predictor are monkeypatched; storage is a temp local dir.
"""
import asyncio
import os
import tempfile
import unittest

from backend.brain import dataset


class TestRunBrainPipeline(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.mkdtemp()
        os.environ["BRAIN_STORAGE_ROOT"] = self._root
        os.environ.pop("STORAGE_BACKEND", None)  # force local adapter
        os.environ["CLOUD_PROVIDER"] = "local"

        from backend.brain import run_brain_pipeline as rbp
        from backend.brain.data import bq_panel

        prices = list(dataset.make_price_series(n=320, seed=1))
        sector = list(dataset.make_price_series(n=320, seed=2))
        self._items = [
            {"ticker": t, "as_of_date": "2026-06-21", "prices": prices,
             "sector_prices": sector, "fundamentals": {}, "feature_row": {}}
            for t in ("AAPL", "MSFT", "NVDA")
        ]
        # Train from the deterministic synthetic panel; serve from real items.
        bq_panel.build_training_panel = lambda **kw: dataset.synthetic_panel(n_tickers=80, n_periods=20)
        bq_panel.build_inference_rows = lambda **kw: list(self._items)
        self._rbp = rbp

    def test_runs_and_persists_snapshots(self):
        status = asyncio.run(self._rbp.run_brain_pipeline(timesfm=False))
        self.assertEqual(status["tickers_total"], 3)
        self.assertEqual(status["tickers_done"], 3)
        self.assertEqual(status["as_of_date"], "2026-06-21")
        self.assertEqual(status["errors"], [])

        # status.json round-trips through storage
        read = self._rbp.read_status()
        self.assertIsNotNone(read)
        self.assertEqual(read["tickers_done"], 3)

        # a snapshot is actually on disk
        from backend.brain.ports.factory import get_storage
        from backend.brain.snapshot_store import SnapshotStore
        store = SnapshotStore(storage=get_storage())
        self.assertTrue(store.exists("AAPL", "2026-06-21"))

    def test_serving_returns_live_contract_and_emits_ledger(self):
        asyncio.run(self._rbp.run_brain_pipeline(timesfm=False))

        emitted = {}

        def _fake_emit(**kwargs):
            emitted.update(kwargs)
            return "did-123"

        import backend.decision_ledger as dl
        import backend.brain.serving as serving
        orig = dl.emit_decision
        orig_price = serving._live_price
        dl.emit_decision = _fake_emit
        serving._live_price = lambda ticker: (None, "test")  # no network
        try:
            # Force the snapshot base price (no network) by passing as_of_date.
            result = serving.serve_ticker("AAPL", as_of_date="2026-06-21", emit=True)
        finally:
            dl.emit_decision = orig
            serving._live_price = orig_price

        self.assertIn(result.get("status"), ("LIVE", "STALE", "INVALID", "INVALID_INPUT"))
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(emitted.get("decision_type"), "brain_verdict")
        self.assertEqual(emitted.get("symbol"), "AAPL")

    def test_serving_missing_snapshot_is_graceful(self):
        from backend.brain.serving import serve_ticker
        out = serve_ticker("ZZZZ", as_of_date="1990-01-01", emit=False)
        self.assertEqual(out["status"], "no_snapshot")


if __name__ == "__main__":
    unittest.main()
