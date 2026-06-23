"""Reflex layer: the dynamic-brain behaviors that fix the analyst flaws."""
import tempfile
import time
import unittest

from backend.brain import agent_explainer, dataset, pipeline
from backend.brain.inference import InferenceEngine
from backend.brain.model_registry import ModelRegistry
from backend.brain.ports.local_adapters import LocalStorage
from backend.brain.reflex import (
    STATUS_BAD_INPUT, STATUS_INVALID, STATUS_LIVE, STATUS_STALE,
    LiveInputs, ReflexEngine,
)
from backend.brain.snapshot_store import build_base_snapshot


def _engine():
    reg = ModelRegistry(root="artifacts", storage=LocalStorage(tempfile.mkdtemp()))
    panel = dataset.synthetic_panel(n_tickers=60, n_periods=18, seed=4)
    pipeline.train_and_register(panel, "v1", reg, model_name="logreg")
    return InferenceEngine(reg, "logreg", "v1")


class TestReflex(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        self.rfx = ReflexEngine(self.engine)
        # Identical stock & sector tails so a market-wide move nets to zero
        # relative move (lets us test sector-relative behavior cleanly).
        self.prices = list(dataset.make_price_series(n=300, seed=1))
        self.prices[-1] = 125.0
        self.sector = list(self.prices)  # identical -> base relative strength ~0
        self.fund = {"pe_ratio": 30.0, "ev_ebitda": 20.0, "fcf_yield": 0.03,
                     "roic": 0.0, "operating_margin": 0.0, "sentiment_score": 0.0,
                     "volatility_3m": 0.0, "capital_flow_score": 0.0}
        self.snap = build_base_snapshot(
            self.engine, "NVDA", "2026-06-21", self.prices, self.sector, self.fund,
            dcf_inputs={"fcf0": 6.0, "growth": 0.12, "years": 5,
                        "terminal_growth": 0.025, "discount_rate": 0.09,
                        "equity_to_ev": 0.9},
        )
        # Pin computed_at so age-based tests are deterministic (not wall-clock).
        self.snap.computed_at = "2026-06-21T00:00:00Z"

    # --- the headline NVDA example -----------------------------------------
    def test_price_jump_drops_valuation_and_dcf_upside(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=125.0 * 1.25,
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_LIVE)
        self.assertIn("business_type", out["business"])
        self.assertEqual(out["valuation"]["business_type"], self.snap.business_type)
        self.assertTrue(out["valuation"]["method_breakdown"])
        self.assertIsNotNone(out["reconciliation_live"])
        # DCF upside collapses because intrinsic is fixed and price rose.
        self.assertLess(out["valuation"]["dcf_upside_live"],
                        out["valuation"]["dcf_upside_at_base"])
        self.assertLess(out["valuation"]["valuation_score_live"],
                        out["valuation"]["valuation_score"])
        # Valuation attractiveness signal falls.
        self.assertLess(out["live"]["signal_scores"]["valuation"],
                        out["base"]["signal_scores"]["valuation"])
        # The model genuinely re-ran on changed inputs (re-aggregated composite).
        self.assertNotEqual(out["live"]["composite_score"], out["base"]["composite_score"])

    def test_price_drop_raises_dcf_upside(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=125.0 * 0.85,
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_LIVE)
        self.assertGreater(out["valuation"]["dcf_upside_live"],
                           out["valuation"]["dcf_upside_at_base"])

    # --- freshness affects confidence, NOT the score (flaw #5) -------------
    def test_freshness_lowers_confidence_not_score(self):
        live = LiveInputs(price=130.0)
        fresh = self.rfx.reflex(self.snap, live, now_iso="2026-06-21T03:00:00Z")  # ~1h
        stale = self.rfx.reflex(self.snap, live, now_iso="2026-06-24T02:00:00Z")  # ~72h
        self.assertEqual(fresh["status"], STATUS_LIVE)
        self.assertEqual(stale["status"], STATUS_LIVE)
        # Score (probability + composite) identical regardless of age.
        self.assertEqual(fresh["live"]["outperform_probability"],
                         stale["live"]["outperform_probability"])
        self.assertEqual(fresh["live"]["composite_score"], stale["live"]["composite_score"])
        # Confidence is strictly lower for the older read.
        self.assertGreater(fresh["confidence_score"], stale["confidence_score"])

    # --- anchor-breaking events invalidate (flaw #3/#4) --------------------
    def test_material_event_invalidates(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=126.0, event_flags=["guidance_cut"],
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_INVALID)
        self.assertIsNone(out["live"])
        self.assertTrue(out["recompute_requested"])
        self.assertTrue(any("guidance_cut" in r for r in out["reasons"]))

    def test_positioning_event_does_not_invalidate(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=126.0, event_flags=["analyst_upgrade"],
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_LIVE)

    def test_rate_move_invalidates_dcf_anchor(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=126.0, rate_move_bps=80,
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_INVALID)
        self.assertTrue(any("discount_rate_moved" in r for r in out["reasons"]))

    def test_excessive_age_marks_stale(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=126.0),
                              now_iso="2026-07-21T03:00:00Z")  # ~30 days later
        self.assertEqual(out["status"], STATUS_STALE)
        self.assertTrue(out["recompute_requested"])

    # --- pure price move does NOT invalidate (it is the reflex's job) ------
    def test_large_price_move_stays_live_with_soft_warning(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=125.0 * 1.5,
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_LIVE)
        self.assertIn("soft_move_warning", out["reasons"])

    # --- corporate action guard (flaw #7) ----------------------------------
    def test_split_is_not_a_crash(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=62.5, split_ratio=2.0,
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_LIVE)
        self.assertAlmostEqual(out["freshness"]["move_since_base"], 0.0, places=4)

    def test_implausible_price_rejected(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=1.0,  # 99% drop, no split
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_BAD_INPUT)
        self.assertIsNone(out["live"])

    # --- market-wide vs idiosyncratic (flaw #8) ----------------------------
    def test_market_wide_move_leaves_relative_thesis_unchanged(self):
        move = 0.25
        # sector moved exactly as much as the stock -> idiosyncratic move ~0
        out = self.rfx.reflex(self.snap, LiveInputs(price=125.0 * (1 + move),
                                                    sector_return_since_base=move,
                                                    as_of="2026-06-22T15:00:00Z"))
        updated = self.rfx._recompute_row(self.snap, 125.0 * (1 + move),
                                          LiveInputs(price=125.0 * (1 + move),
                                                     sector_return_since_base=move))
        base_rs = self.snap.base_feature_row.get("relative_strength_3m")
        self.assertIsNotNone(updated["relative_strength_3m"])
        self.assertAlmostEqual(updated["relative_strength_3m"], base_rs or 0.0, places=4)

    # --- waterfall + grounding (flaw #11) ----------------------------------
    def test_waterfall_consistent_and_timestamped(self):
        out = self.rfx.reflex(self.snap, LiveInputs(price=140.0,
                                                    as_of="2026-06-22T15:00:00Z"))
        self.assertTrue(len(out["waterfall"]) >= 3)
        for row in out["waterfall"]:
            self.assertAlmostEqual(row["delta"], row["current"] - row["base"], places=3)
        self.assertEqual(out["computed_at"], self.snap.computed_at)
        self.assertEqual(out["live_as_of"], "2026-06-22T15:00:00Z")

    def test_reflex_explanation_is_grounded(self):
        for live in (LiveInputs(price=156.0, as_of="2026-06-22T15:00:00Z"),
                     LiveInputs(price=126.0, event_flags=["guidance_cut"],
                                as_of="2026-06-22T15:00:00Z")):
            out = self.rfx.reflex(self.snap, live)
            text = agent_explainer.generate_reflex_explanation(out)
            res = agent_explainer.verify_grounding(text, out)
            self.assertTrue(res["grounded"],
                            msg=f"ungrounded {res['ungrounded_numbers']} in: {text}")

    # --- TimesFM forward forecast stays fresh under the Reflex layer -------
    def test_timesfm_forward_return_recomputed_live(self):
        bands = [{"horizon": "63d", "q10": 118.0, "q50": 140.0, "q90": 165.0}]
        snap = build_base_snapshot(
            self.engine, "NVDA", "2026-06-21", self.prices, self.sector, self.fund,
            dcf_inputs={"fcf0": 6.0, "growth": 0.12, "years": 5,
                        "terminal_growth": 0.025, "discount_rate": 0.09},
            timesfm_bands=bands, timesfm_model_version="timesfm-2.5-200m",
        )
        snap.computed_at = "2026-06-21T00:00:00Z"
        out = self.rfx.reflex(snap, LiveInputs(price=125.0 * 1.25,
                                               as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_LIVE)
        base_er = out["timeseries"]["base"]["expected_return"]
        live_er = out["timeseries"]["live"]["expected_return"]
        # bands fixed, price up -> TimesFM forward return shrinks (and goes negative)
        self.assertLess(live_er, base_er)
        self.assertLess(live_er, 0)
        # the live block carries the TimesFM forecast for the UI
        self.assertEqual(out["live"]["timeseries_forecast"]["source"], "timesfm")
        # waterfall includes a TimesFM expected-return bridge row
        self.assertTrue(any(w["component"] == "tsfm_expected_return"
                            for w in out["waterfall"]))

    # --- ledger emit hook (flaw #9) ----------------------------------------
    def test_ledger_emit_hook_called(self):
        emitted = []
        rfx = ReflexEngine(self.engine, emit_fn=lambda c: emitted.append(c["status"]))
        rfx.reflex(self.snap, LiveInputs(price=130.0, as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0], STATUS_LIVE)

    def test_ledger_emit_failure_never_breaks(self):
        def boom(_c):
            raise RuntimeError("ledger down")
        rfx = ReflexEngine(self.engine, emit_fn=boom)
        out = rfx.reflex(self.snap, LiveInputs(price=130.0, as_of="2026-06-22T15:00:00Z"))
        self.assertEqual(out["status"], STATUS_LIVE)  # still returns normally


if __name__ == "__main__":
    unittest.main()
