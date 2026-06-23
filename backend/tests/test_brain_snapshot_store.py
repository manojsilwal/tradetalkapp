"""Snapshot store: anchors persist and round-trip (offline)."""
import tempfile
import unittest

from backend.brain import dataset, pipeline
from backend.brain.inference import InferenceEngine
from backend.brain.model_registry import ModelRegistry
from backend.brain.ports.local_adapters import LocalStorage
from backend.brain.snapshot_store import SnapshotStore, build_base_snapshot


def _engine():
    reg = ModelRegistry(root="artifacts", storage=LocalStorage(tempfile.mkdtemp()))
    panel = dataset.synthetic_panel(n_tickers=60, n_periods=18, seed=2)
    pipeline.train_and_register(panel, "v1", reg, model_name="logreg")
    return InferenceEngine(reg, "logreg", "v1"), panel


class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        self.engine, self.panel = _engine()
        self.prices = list(dataset.make_price_series(n=300, seed=1))
        self.sector = list(dataset.make_price_series(n=300, seed=2))
        self.fund = {"pe_ratio": 22.0, "ev_ebitda": 14.0, "fcf_yield": 0.04,
                     "roic": 0.18, "operating_margin": 0.25, "sentiment_score": 0.1}

    def _build(self):
        return build_base_snapshot(
            self.engine, "AAPL", "2026-06-21", self.prices, self.sector, self.fund,
            dcf_inputs={"fcf0": 6.0, "growth": 0.10, "years": 5,
                        "terminal_growth": 0.025, "discount_rate": 0.09,
                        "equity_to_ev": 0.9},
            sector="Tech", fundamentals_as_of="2026-03-31",
        )

    def test_build_has_anchors(self):
        snap = self._build()
        self.assertEqual(snap.base_price, self.prices[-1])
        self.assertTrue(len(snap.price_tail) >= 253)
        self.assertTrue(len(snap.sector_ref_tail) >= 253)
        self.assertIsNotNone(snap.intrinsic_value_mid)
        self.assertIsNotNone(snap.dcf_upside_at_base)
        self.assertAlmostEqual(snap.discount_rate, 0.09)
        self.assertEqual(snap.sector, "Tech")
        self.assertIsNotNone(snap.business_type)
        self.assertEqual(snap.valuation_status, "ok")
        self.assertTrue(snap.valuation_method_breakdown)
        self.assertIsNotNone(snap.margin_of_safety_base)
        self.assertIsNotNone(snap.valuation_score)
        self.assertIsNotNone(snap.reverse_dcf)
        self.assertIsNotNone(snap.reconciliation)
        # base contract carries the non-negotiable stamps
        self.assertEqual(snap.base_contract["model_version"], "v1")
        self.assertIn("disclaimer", snap.base_contract)

    def test_save_load_roundtrip(self):
        store = SnapshotStore(root="predictions", storage=LocalStorage(tempfile.mkdtemp()))
        snap = self._build()
        self.assertFalse(store.exists("AAPL", "2026-06-21"))
        store.save(snap)
        self.assertTrue(store.exists("AAPL", "2026-06-21"))
        loaded = store.load("AAPL", "2026-06-21")
        self.assertEqual(loaded.ticker, "AAPL")
        self.assertEqual(loaded.base_price, snap.base_price)
        self.assertEqual(loaded.intrinsic_value_mid, snap.intrinsic_value_mid)
        self.assertEqual(loaded.price_tail, snap.price_tail)
        self.assertEqual(loaded.base_feature_row, snap.base_feature_row)
        self.assertEqual(loaded.business_type, snap.business_type)
        self.assertEqual(loaded.valuation_method_breakdown, snap.valuation_method_breakdown)

    def test_no_dcf_inputs_leaves_valuation_none(self):
        snap = build_base_snapshot(self.engine, "X", "2026-06-21", self.prices,
                                   self.sector, self.fund)
        self.assertIsNone(snap.intrinsic_value_mid)
        self.assertIsNone(snap.dcf_upside_at_base)
        self.assertEqual(snap.valuation_status, "insufficient_data")

    def test_router_uses_fundamentals_without_legacy_dcf_inputs(self):
        fund = dict(self.fund)
        fund.update({"fcf_per_share": 5.0, "fcf_growth": 0.08,
                     "discount_rate": 0.09, "market_cap": 250e9,
                     "fcf_margin": 0.22, "gross_margin": 0.55})
        snap = build_base_snapshot(self.engine, "MSFT", "2026-06-21", self.prices,
                                   self.sector, fund, sector="Tech")
        self.assertEqual(snap.valuation_status, "ok")
        self.assertIsNotNone(snap.intrinsic_value_mid)
        self.assertTrue(any(m["method"] == "owner_earnings_dcf"
                            for m in snap.valuation_method_breakdown))

    def test_timesfm_bands_anchor_and_features(self):
        bands = [{"horizon": "63d", "q10": 118.0, "q50": 140.0, "q90": 165.0}]
        snap = build_base_snapshot(
            self.engine, "AAPL", "2026-06-21", self.prices, self.sector, self.fund,
            timesfm_bands=bands, timesfm_model_version="timesfm-2.5-200m",
        )
        # bands stored as anchors so the Reflex layer can recompute live
        self.assertEqual(snap.timesfm_bands, bands)
        self.assertEqual(snap.timesfm_model_version, "timesfm-2.5-200m")
        self.assertIsNotNone(snap.timeseries_forecast)
        # the TimesFM feature was injected into the base feature row (model uses it)
        self.assertIsNotNone(snap.base_feature_row["tsfm_expected_return"])
        self.assertAlmostEqual(snap.base_feature_row["tsfm_expected_return"],
                               140.0 / snap.base_price - 1.0)

    def test_timesfm_persists_through_store(self):
        bands = [{"horizon": "63d", "q10": 118.0, "q50": 140.0, "q90": 165.0}]
        snap = build_base_snapshot(self.engine, "AAPL", "2026-06-21", self.prices,
                                   self.sector, self.fund, timesfm_bands=bands)
        store = SnapshotStore(root="predictions", storage=LocalStorage(tempfile.mkdtemp()))
        store.save(snap)
        loaded = store.load("AAPL", "2026-06-21")
        self.assertEqual(loaded.timesfm_bands, bands)


if __name__ == "__main__":
    unittest.main()
