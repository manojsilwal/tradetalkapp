"""Offline tests for the Picks & Shovels scoring + taxonomy (pure functions)."""
from __future__ import annotations

import unittest

from backend.picks_shovels import scoring, themes


def _raw(ticker, *, ret=10.0, rev=20.0, gm=45.0, mcap=5e10, capex=90.0, sector="Technology"):
    return {
        "ticker": ticker,
        "company_name": ticker,
        "sector": sector,
        "momentum": {
            "ret_3m_pct": ret, "ret_6m_pct": ret, "ret_12m_pct": ret,
            "above_50dma_pct": 5.0, "above_200dma_pct": 8.0, "vol_ratio": 1.2,
        },
        "fundamentals": {
            "revenue_growth_pct": rev, "earnings_growth_pct": rev,
            "gross_margin_pct": gm, "operating_margin_pct": gm / 2.0,
            "fcf_yield_pct": 3.0, "forward_pe": 25.0, "debt_to_equity": 0.5,
            "market_cap": mcap, "sector": sector,
        },
        "operating": {"available": False},
        "evidence": {"available": False},
        "theme": {"themes": ["memory_hbm"], "bottleneck_solved": "HBM",
                  "hiddenness_seed": "", "customer_capex_seed": capex},
    }


class TestTaxonomy(unittest.TestCase):
    def test_validate(self):
        themes.validate_taxonomy()  # raises on inconsistency

    def test_universe_nonempty_and_includes_hidden(self):
        self.assertGreater(len(themes.SEED_UNIVERSE), 50)
        for tk in ("TTMI", "BELFB", "CAMT", "AAOI", "POWL", "MOD"):
            self.assertIn(tk, themes.SEED_UNIVERSE)

    def test_membership_has_theme_and_bottleneck(self):
        m = themes.membership_for("MU")
        self.assertIn("memory_hbm", m.themes)
        self.assertTrue(m.bottleneck_solved)


class TestPercentile(unittest.TestCase):
    def test_rank_basic(self):
        pop = [0.0, 10.0, 20.0, 30.0, 40.0]
        self.assertEqual(scoring.percentile_rank(40.0, pop), 100.0)
        self.assertEqual(scoring.percentile_rank(0.0, pop), 20.0)
        self.assertIsNone(scoring.percentile_rank(None, pop))
        self.assertIsNone(scoring.percentile_rank(5.0, []))


class TestWeights(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(scoring.WEIGHTS.values()), 1.0, places=6)


class TestComponentScores(unittest.TestCase):
    def test_backlog_neutral_when_unavailable(self):
        self.assertEqual(scoring.backlog_rpo_score({"available": False}), scoring.NEUTRAL)

    def test_bottleneck_neutral_when_unavailable(self):
        self.assertEqual(scoring.bottleneck_evidence_score({"available": False}), scoring.NEUTRAL)

    def test_customer_capex_uses_seed(self):
        self.assertEqual(scoring.customer_capex_exposure_score({"customer_capex_seed": 88.0}), 88.0)

    def test_valuation_penalizes_cyclical(self):
        base = scoring.valuation_risk_score({"forward_pe": 25, "revenue_growth_pct": 20, "fcf_yield_pct": 3, "debt_to_equity": 0.5, "sector": "Technology"})
        cyc = scoring.valuation_risk_score({"forward_pe": 25, "revenue_growth_pct": 20, "fcf_yield_pct": 3, "debt_to_equity": 0.5, "sector": "Energy"})
        self.assertLess(cyc, base)


class TestScoreRow(unittest.TestCase):
    def test_strong_outranks_weak(self):
        rows = [
            _raw("STRONG", ret=35.0, rev=45.0, gm=65.0, mcap=2e11),
            _raw("MID", ret=8.0, rev=12.0, gm=40.0, mcap=3e10),
            _raw("WEAK", ret=-10.0, rev=2.0, gm=25.0, mcap=4e9),
        ]
        ctx = scoring.PercentileContext.build(rows)
        scored = {r["ticker"]: scoring.score_row(r, ctx) for r in rows}
        self.assertGreater(scored["STRONG"]["final_score"], scored["MID"]["final_score"])
        self.assertGreater(scored["MID"]["final_score"], scored["WEAK"]["final_score"])
        for s in scored.values():
            self.assertFalse(s["insufficient_data"])
            self.assertTrue(0 <= s["final_score"] <= 100)

    def test_insufficient_when_no_inputs(self):
        raw = {
            "ticker": "EMPTY", "momentum": {}, "fundamentals": {},
            "operating": {"available": False}, "evidence": {"available": False},
            "theme": {"customer_capex_seed": 60.0},
        }
        ctx = scoring.PercentileContext.build([raw])
        out = scoring.score_row(raw, ctx)
        self.assertTrue(out["insufficient_data"])

    def test_hiddenness_by_market_cap(self):
        big = scoring.classify_hiddenness(2e11, "")
        sec = scoring.classify_hiddenness(3e10, "")
        hid = scoring.classify_hiddenness(4e9, "")
        self.assertEqual(big["hiddenness_level"], "Big Player")
        self.assertEqual(sec["hiddenness_level"], "Secondary Player")
        self.assertEqual(hid["hiddenness_level"], "Hidden Player")

    def test_hiddenness_seed_overrides(self):
        out = scoring.classify_hiddenness(2e11, "Hidden Player")
        self.assertEqual(out["hiddenness_level"], "Hidden Player")

    def test_confidence_thresholds(self):
        self.assertEqual(scoring.confidence_level(1.0, 4)["confidence_level"], "High")
        self.assertEqual(scoring.confidence_level(0.0, 0)["confidence_level"], "Low")


if __name__ == "__main__":
    unittest.main()
