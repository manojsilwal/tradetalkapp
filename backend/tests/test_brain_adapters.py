"""Brain → legacy-surface adapters, memo, flags, and cutover gating (offline)."""
import os
import unittest

from backend.brain import adapters, flags, memo


def _result(p, composite=70.0, status="LIVE"):
    block = {
        "outperform_probability": p,
        "composite_score": composite,
        "risk_score": 0.3,
        "signal_scores": {"momentum": 80, "quality": 65, "valuation": 55},
        "drivers": {"supporting": ["strong 3-month price momentum", "high return on invested capital"],
                    "detracting": ["rich P/E valuation"]},
        "recommendation": "constructive" if p >= 0.6 else "neutral",
    }
    return {
        "ticker": "AAPL",
        "status": status,
        "model_version": "v1",
        "confidence_score": 0.7,
        "live": block,
        "base": {**block, "horizon_days": 63, "recommendation": block["recommendation"]},
        "valuation": {"intrinsic_value_mid": 180.0, "dcf_upside_at_base": 0.12,
                      "dcf_upside_live": 0.10},
        "reconciliation": {"quadrant": "value_trap"},
        "timeseries": {"live": {"expected_return": 0.05}},
    }


class TestAdapters(unittest.TestCase):
    def test_verdict_thresholds(self):
        self.assertEqual(adapters.verdict_5(_result(0.80)), "Strong Buy")
        self.assertEqual(adapters.verdict_5(_result(0.60)), "Buy")
        self.assertEqual(adapters.verdict_5(_result(0.50)), "Hold")
        self.assertEqual(adapters.verdict_5(_result(0.40)), "Sell")
        self.assertEqual(adapters.verdict_5(_result(0.20)), "Strong Sell")

    def test_stale_anchor_downgrades_strong_verdicts(self):
        r = _result(0.20)
        r["valuation"]["base_price"] = 80.0
        r["valuation"]["live_price"] = 198.0
        r["freshness"] = {"move_since_base": 1.475}
        self.assertEqual(adapters.verdict_5(r), "Sell")
        r2 = _result(0.80)
        r2["valuation"]["base_price"] = 80.0
        r2["valuation"]["live_price"] = 198.0
        r2["freshness"] = {"move_since_base": 1.475}
        self.assertEqual(adapters.verdict_5(r2), "Buy")

    def test_verdict_4_collapses_strong_sell(self):
        self.assertEqual(adapters.verdict_4(_result(0.20)), "Sell")

    def test_swarm_verdict_uppercase(self):
        self.assertEqual(adapters.swarm_verdict(_result(0.80)), "STRONG BUY")

    def test_actionable_row(self):
        row = adapters.to_actionable_row(_result(0.80))
        self.assertEqual(row["verdict"], "Strong Buy")
        self.assertTrue(row["actionable"])
        self.assertEqual(row["score"], 70.0)

    def test_scorecard_fields(self):
        sc = adapters.to_scorecard_fields(_result(0.80))
        self.assertEqual(sc["signal"], "Strong Buy")
        self.assertEqual(sc["action"], "Buy")
        self.assertEqual(sc["quadrant"], "value_trap")
        self.assertIn("Strong Buy", sc["one_line_reason"])

    def test_daily_brief_verdict(self):
        v = adapters.to_daily_brief_verdict(_result(0.30))
        self.assertEqual(v["verdict"], "Sell")
        self.assertEqual(v["verdict_tier"], "brain")
        # Numeric blend fields must be present so the frontend can show scores.
        self.assertIsNotNone(v["outperform_probability"])
        self.assertIsNotNone(v["composite_score"])
        self.assertIsNotNone(v["signal_scores"])
        self.assertAlmostEqual(v["outperform_probability"], 0.30)
        self.assertEqual(v["composite_score"], 70.0)
        self.assertEqual(v["confidence_score"], 0.7)

    def test_decision_terminal_headline(self):
        h = adapters.to_decision_terminal_headline(_result(0.80))
        self.assertEqual(h["headline_verdict"], "Strong Buy")
        self.assertEqual(h["intrinsic_value_mid"], 180.0)
        self.assertEqual(h["dcf_upside"], 0.10)


class TestMemo(unittest.TestCase):
    def test_build_memo_is_grounded(self):
        from backend.brain import agent_explainer as ax
        r = _result(0.72)
        m = memo.build_memo(r)
        self.assertEqual(m["verdict"], "Strong Buy")
        self.assertEqual(m["stance"], "bull")
        self.assertEqual(len(m["arguments"]), 2)
        # the deterministic summary must be grounded in the result
        self.assertTrue(ax.verify_grounding(m["summary"], r)["grounded"])


class TestFlagsAndCutover(unittest.TestCase):
    def setUp(self):
        for k in list(os.environ):
            if k.startswith("BRAIN_CUTOVER") or k == "BRAIN_SERVE_ENABLE":
                os.environ.pop(k, None)

    def tearDown(self):
        self.setUp()

    def test_disabled_by_default(self):
        self.assertFalse(flags.brain_surface_enabled("scorecard"))

    def test_requires_serving_enabled(self):
        os.environ["BRAIN_CUTOVER_ALL"] = "1"
        self.assertFalse(flags.brain_surface_enabled("scorecard"))
        os.environ["BRAIN_SERVE_ENABLE"] = "1"
        self.assertTrue(flags.brain_surface_enabled("scorecard"))

    def test_per_surface_flag(self):
        os.environ["BRAIN_SERVE_ENABLE"] = "1"
        os.environ["BRAIN_CUTOVER_SCORECARD"] = "1"
        self.assertTrue(flags.brain_surface_enabled("scorecard"))
        self.assertFalse(flags.brain_surface_enabled("debate"))

    def test_cutover_returns_none_when_disabled(self):
        from backend.brain.cutover import serve_for_surface
        self.assertIsNone(serve_for_surface("AAPL", "scorecard"))


if __name__ == "__main__":
    unittest.main()
