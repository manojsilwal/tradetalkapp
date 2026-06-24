"""Long-horizon investment surface (offline).

Covers the adopted improvement plan items: 12-month minimum horizon, long-horizon
re-weighting (pricing context capped small), investment-stance vocabulary, the
Valuation Freshness Monitor (reusing Reflex), and the critical invariant that a
price drop is read as improved margin of safety — NOT a stance cap.
"""
import unittest

from backend.brain import (
    INVESTMENT_HORIZON_DAYS,
    MIN_INVESTMENT_HORIZON_MONTHS,
    SIGNAL_GROUPS,
)
from backend.brain import investment_stance as inv
from backend.brain import labels, rule_baseline


_FULL_GROUPS = {
    "valuation": 80.0, "quality": 75.0, "risk": 70.0, "capital_flow": 60.0,
    "filing_intelligence": 65.0, "timeseries": 55.0, "momentum": 90.0, "sentiment": 50.0,
}

_TRADING_VERBS = ("stop loss", "stop-loss", "take profit", "take-profit", "scalp",
                  "intraday", "enter now", "trade setup")


def _brain_result(*, groups=None, status="LIVE", base_price=200.0, live_price=190.0,
                  confidence=0.7, business_type="wide_moat_compounder",
                  move_since_base=None, reasons=None):
    groups = _FULL_GROUPS if groups is None else groups
    block = {
        "outperform_probability": 0.62,
        "composite_score": 68.0,
        "risk_score": 0.3,
        "signal_scores": dict(groups),
        "recommendation": "constructive",
        "drivers": {"supporting": ["attractive free-cash-flow yield"], "detracting": []},
        "live_price": live_price,
    }
    fresh = {}
    if move_since_base is not None:
        fresh = {"move_since_base": move_since_base}
    return {
        "ticker": "AAPL",
        "as_of_date": "2026-06-20",
        "status": status,
        "model_name": "finrank-net",
        "model_version": "v1",
        "confidence_score": confidence,
        "reasons": reasons or [],
        "live": block,
        "base": dict(block),
        "freshness": fresh,
        "valuation": {
            "base_price": base_price, "live_price": live_price,
            "business_type": business_type,
            "intrinsic_value_mid": 235.0, "valuation_status": "moderately_undervalued",
            "status": "moderately_undervalued",
            "dcf_upside_at_base": 0.18, "dcf_upside_live": 0.24,
        },
        "business": {"business_type": business_type},
    }


class TestHorizonAndWeights(unittest.TestCase):
    def test_minimum_horizon_is_12_months(self):
        meta = inv.investment_horizon_meta()
        self.assertGreaterEqual(meta["minimum_horizon_months"], 12)
        self.assertEqual(MIN_INVESTMENT_HORIZON_MONTHS, 12)
        self.assertEqual(meta["analysis_type"], "investment_research")
        self.assertEqual(INVESTMENT_HORIZON_DAYS["1y"], 252)

    def test_long_horizon_weights_sum_to_one_over_groups(self):
        w = rule_baseline.LONG_HORIZON_COMPOSITE_WEIGHTS
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
        self.assertEqual(set(w.keys()), set(SIGNAL_GROUPS))

    def test_pricing_context_weight_capped_small(self):
        # momentum + sentiment are "pricing context only" and must stay tiny.
        w = rule_baseline.LONG_HORIZON_COMPOSITE_WEIGHTS
        self.assertLessEqual(w["momentum"], 0.05)
        self.assertLessEqual(w["sentiment"], 0.05)
        self.assertGreaterEqual(w["valuation"], 0.30)

    def test_momentum_swing_barely_moves_score(self):
        hi = rule_baseline.composite_from_group_scores(
            {**_FULL_GROUPS, "momentum": 100.0}, rule_baseline.LONG_HORIZON_COMPOSITE_WEIGHTS)
        lo = rule_baseline.composite_from_group_scores(
            {**_FULL_GROUPS, "momentum": 0.0}, rule_baseline.LONG_HORIZON_COMPOSITE_WEIGHTS)
        # A 100-point momentum swing may not move the long-horizon score > 5 pts.
        self.assertLessEqual(abs(hi - lo), 5.0)

    def test_composite_from_group_scores_none_when_empty(self):
        self.assertIsNone(rule_baseline.composite_from_group_scores({}))


class TestLabelsMultiHorizon(unittest.TestCase):
    def test_horizon_days_for(self):
        self.assertEqual(labels.horizon_days_for("3y"), 756)
        self.assertEqual(labels.horizon_days_for("unknown"), 63)  # safe default

    def test_build_multi_horizon_labels(self):
        prices = list(range(100, 1100))
        bench = list(range(100, 1100))
        panel = [{"ticker": "A", "as_of_idx": 10, "prices": prices, "benchmark": bench}]
        out = labels.build_multi_horizon_labels(panel, [63, 252, 756])
        self.assertEqual(set(out), {63, 252, 756})
        self.assertIsNotNone(out[252][0])


class TestStanceVocabulary(unittest.TestCase):
    def test_all_outputs_use_allowed_stances(self):
        for p_status in ("LIVE", "STALE", "INVALID", "INVALID_INPUT"):
            r = _brain_result(status=p_status,
                              reasons=["material_event:earnings"] if p_status == "INVALID" else
                                      (["stale_age:200h"] if p_status == "STALE" else []))
            out = inv.build_investment_analysis(r)
            self.assertIn(out["final"]["stance"], inv.ALLOWED_STANCES)

    def test_no_trading_verbs_in_output(self):
        out = inv.build_investment_analysis(_brain_result())
        # The guardrail/disclaimer intentionally NAME prohibited trading terms
        # ("do not produce ... stop losses"), so scan only the recommendation-
        # bearing fields, not the guardrail copy.
        memo = {k: v for k, v in out["memo"].items() if k not in ("guardrail", "disclaimer")}
        scanned = {
            "final": out["final"],
            "pricing_context": out["pricing_context"],
            "valuation_freshness": out["valuation_freshness"],
            "risk_committee": out["risk_committee"],
            "memo": memo,
        }
        blob = str(scanned).lower()
        for verb in _TRADING_VERBS:
            self.assertNotIn(verb, blob, f"trading verb leaked: {verb}")

    def test_insufficient_evidence_when_no_group_scores(self):
        out = inv.build_investment_analysis(_brain_result(groups={}))
        self.assertEqual(out["final"]["stance"], inv.STANCE_INSUFFICIENT)
        self.assertIsNone(out["final"]["investment_score"])


class TestPriceMoveNeverCaps(unittest.TestCase):
    """The core critique fix: a price drop improves margin of safety; it must
    NOT cap the stance at Hold/Watch."""

    def test_large_drop_does_not_cap_stance(self):
        # Strong long-horizon fundamentals, price -25% on unchanged thesis.
        r = _brain_result(base_price=200.0, live_price=150.0, move_since_base=-0.25,
                          groups={**_FULL_GROUPS, "valuation": 92.0, "quality": 85.0})
        out = inv.build_investment_analysis(r)
        self.assertIn(out["final"]["stance"],
                      ("Strong Long-Term Buy", "Buy / Accumulate", "Accumulate Slowly"))
        self.assertNotEqual(out["final"]["stance"], "Hold / Watch")

    def test_drop_flagged_as_improved_margin_of_safety(self):
        r = _brain_result(base_price=200.0, live_price=150.0, move_since_base=-0.25)
        pc = inv.build_investment_analysis(r)["pricing_context"]
        self.assertTrue(pc["margin_of_safety_changed"])
        self.assertIn("improving the margin of safety", pc["pricing_note"])

    def test_large_move_asks_for_live_repricing_not_refresh(self):
        r = _brain_result(move_since_base=-0.25)
        vf = inv.build_investment_analysis(r)["valuation_freshness"]
        self.assertEqual(vf["status"], "needs_live_repricing")
        self.assertFalse(vf["requires_full_valuation_refresh"])


class TestFreshnessAndGates(unittest.TestCase):
    def test_material_event_requires_full_refresh(self):
        r = _brain_result(status="INVALID", reasons=["material_event:earnings"])
        out = inv.build_investment_analysis(r)
        self.assertEqual(out["final"]["stance"], inv.STANCE_REFRESH)
        self.assertTrue(out["valuation_freshness"]["requires_full_valuation_refresh"])
        self.assertTrue(out["valuation_freshness"]["requires_event_review"])

    def test_stale_age_keeps_thesis_but_queues_recompute(self):
        r = _brain_result(status="STALE", reasons=["stale_age:200h"])
        out = inv.build_investment_analysis(r)
        self.assertEqual(out["valuation_freshness"]["status"], "stale_recompute_queued")
        self.assertFalse(out["valuation_freshness"]["requires_full_valuation_refresh"])
        # A stale-by-age snapshot is NOT forced to Pending Refresh.
        self.assertNotEqual(out["final"]["stance"], inv.STANCE_REFRESH)

    def test_low_confidence_caps_at_hold(self):
        r = _brain_result(confidence=0.2, groups={**_FULL_GROUPS, "valuation": 95.0})
        out = inv.build_investment_analysis(r)
        self.assertEqual(out["final"]["stance"], "Hold / Watch")

    def test_high_growth_weak_balance_sheet_is_speculative(self):
        r = _brain_result(business_type="high_growth_unprofitable",
                          groups={**_FULL_GROUPS, "risk": 30.0})
        out = inv.build_investment_analysis(r)
        self.assertEqual(out["final"]["stance"], "Speculative / High Risk")


class TestMemoGuardrail(unittest.TestCase):
    def test_memo_has_required_sections_and_guardrail(self):
        out = inv.build_investment_analysis(_brain_result())
        memo = out["memo"]
        for key in ("investment_horizon", "business_valuation_view", "pricing_context_view",
                    "recent_event_freshness_view", "risk_committee_view",
                    "final_long_term_stance"):
            self.assertIn(key, memo)
        self.assertIn("12-month", inv.INVESTMENT_GUARDRAIL)
        self.assertEqual(out["minimum_horizon_months"], 12)


if __name__ == "__main__":
    unittest.main()
