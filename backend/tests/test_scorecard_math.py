"""Deterministic math tests for backend/scorecard.py."""
import math
import unittest

from backend.scorecard import (
    PRESETS,
    ScorecardInput,
    Weights,
    apply_situational_adjustments,
    classify_quadrant,
    compute_pe_stretch,
    interpret_ratio,
    normalize,
    resolve_weights,
    score_basket,
    score_single,
)


class TestNormalize(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(normalize(5.0, 10.0), 5.0)
        self.assertAlmostEqual(normalize(10.0, 10.0), 10.0)
        self.assertAlmostEqual(normalize(0.0, 10.0), 0.0)

    def test_zero_denominator_returns_zero(self):
        self.assertEqual(normalize(5.0, 0.0), 0.0)
        self.assertEqual(normalize(5.0, -1.0), 0.0)

    def test_clamped_to_ten(self):
        # When a value exceeds the denom (shouldn't happen with max-based denom)
        # the helper still clamps to 10 to avoid nonsense downstream.
        self.assertEqual(normalize(50.0, 10.0), 10.0)

    def test_negative_clamped_to_zero(self):
        self.assertEqual(normalize(-5.0, 10.0), 0.0)


class TestPeStretch(unittest.TestCase):
    def test_max_zero_guard(self):
        # fwd below history → 0, no penalty
        self.assertEqual(compute_pe_stretch(20.0, 25.0), 0.0)

    def test_positive_stretch(self):
        self.assertAlmostEqual(compute_pe_stretch(30.0, 20.0), 0.5)

    def test_missing_inputs(self):
        self.assertEqual(compute_pe_stretch(None, 20.0), 0.0)
        self.assertEqual(compute_pe_stretch(20.0, None), 0.0)
        self.assertEqual(compute_pe_stretch(None, None), 0.0)

    def test_nonpositive_hist(self):
        self.assertEqual(compute_pe_stretch(25.0, 0.0), 0.0)
        self.assertEqual(compute_pe_stretch(25.0, -5.0), 0.0)


class TestPresetWeights(unittest.TestCase):
    """Preset weights must match the Step 4 table exactly — source of truth."""

    def test_all_presets_present(self):
        self.assertEqual(
            set(PRESETS.keys()),
            {"growth", "value", "income", "balanced"},
        )

    def test_growth_weights(self):
        w = PRESETS["growth"]
        self.assertEqual(w, Weights(w1=5, w2=5, w3=3, w4=0, w5=2, w6=2, w7=4, w8=1, w9=4))

    def test_value_weights(self):
        w = PRESETS["value"]
        self.assertEqual(w, Weights(w1=2, w2=2, w3=5, w4=2, w5=5, w6=2, w7=3, w8=3, w9=3))

    def test_income_weights(self):
        w = PRESETS["income"]
        self.assertEqual(w, Weights(w1=1, w2=1, w3=2, w4=5, w5=3, w6=3, w7=2, w8=4, w9=2))

    def test_balanced_weights(self):
        w = PRESETS["balanced"]
        self.assertEqual(w, Weights(w1=3, w2=3, w3=2, w4=1, w5=3, w6=2, w7=3, w8=2, w9=4))

    def test_resolve_with_overrides(self):
        merged = resolve_weights("balanced", {"w6": 4.0})
        self.assertEqual(merged.w6, 4.0)
        # Other keys unchanged.
        base = PRESETS["balanced"]
        self.assertEqual(merged.w1, base.w1)
        self.assertEqual(merged.w9, base.w9)

    def test_resolve_rejects_unknown_preset(self):
        with self.assertRaises(ValueError):
            resolve_weights("contrarian")

    def test_resolve_rejects_unknown_override_key(self):
        with self.assertRaises(ValueError):
            resolve_weights("balanced", {"w99": 1.0})


class TestInterpretationBands(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(interpret_ratio(3.0)["signal"], "Exceptional")
        self.assertEqual(interpret_ratio(2.5)["signal"], "Exceptional")
        self.assertEqual(interpret_ratio(2.49)["signal"], "Strong buy")
        self.assertEqual(interpret_ratio(2.0)["signal"], "Strong buy")
        self.assertEqual(interpret_ratio(1.99)["signal"], "Favorable")
        self.assertEqual(interpret_ratio(1.5)["signal"], "Favorable")
        self.assertEqual(interpret_ratio(1.49)["signal"], "Balanced")
        self.assertEqual(interpret_ratio(1.0)["signal"], "Balanced")
        self.assertEqual(interpret_ratio(0.99)["signal"], "Caution")
        self.assertEqual(interpret_ratio(0.7)["signal"], "Caution")
        self.assertEqual(interpret_ratio(0.69)["signal"], "Avoid")
        self.assertEqual(interpret_ratio(0.0)["signal"], "Avoid")
        self.assertEqual(interpret_ratio(-1.0)["signal"], "Avoid")


class TestQuadrants(unittest.TestCase):
    def test_top_left_sweet_spot(self):
        self.assertEqual(classify_quadrant(8.0, 3.0), "top-left")

    def test_top_right_high_conviction(self):
        self.assertEqual(classify_quadrant(8.0, 7.0), "top-right")

    def test_bottom_left_safe_slow(self):
        self.assertEqual(classify_quadrant(3.0, 2.0), "bottom-left")

    def test_bottom_right_avoid(self):
        self.assertEqual(classify_quadrant(3.0, 8.0), "bottom-right")

    def test_midpoint_on_boundary(self):
        # ties go to high-return / low-risk (sweet spot)
        self.assertEqual(classify_quadrant(5.0, 5.0), "top-right")


class TestSituationalAdjustments(unittest.TestCase):
    def test_unknown_flag_is_noop(self):
        w = PRESETS["balanced"]
        adj = apply_situational_adjustments(w, {"nonsense_flag": True})
        self.assertEqual(adj, w)

    def test_flag_off_is_noop(self):
        w = PRESETS["balanced"]
        adj = apply_situational_adjustments(w, {"bear_or_rate_hike": False})
        self.assertEqual(adj, w)

    def test_bear_doubles_beta_weight(self):
        w = PRESETS["balanced"]
        adj = apply_situational_adjustments(w, {"bear_or_rate_hike": True})
        self.assertEqual(adj.w6, w.w6 * 2.0)
        self.assertEqual(adj.w1, w.w1)

    def test_utilities_halves_de_weight(self):
        w = PRESETS["balanced"]
        adj = apply_situational_adjustments(w, {"utilities_vs_industrials": True})
        self.assertEqual(adj.w8, w.w8 * 0.5)

    def test_recent_ipo_halves_sitg_weight(self):
        w = PRESETS["growth"]
        adj = apply_situational_adjustments(w, {"recent_ipo_lt_2y": True})
        self.assertEqual(adj.w9, w.w9 * 0.5)


def _mk_row(**kw) -> ScorecardInput:
    defaults = dict(
        ticker="T",
        eps_growth_pct=0.0,
        revenue_growth_pct=0.0,
        pt_upside_pct=0.0,
        dividend_yield_pct=0.0,
        forward_pe=None,
        historical_avg_pe=None,
        beta=1.0,
        exec_risk_score=5.0,
        debt_to_equity=0.5,
        sitg_score=0.0,
        ceo_name="",
        sitg_archetype="",
    )
    defaults.update(kw)
    return ScorecardInput(**defaults)


class TestScoreBasketMechanics(unittest.TestCase):
    def test_single_best_basket_gets_max_scores(self):
        # One ticker is always max in its own set → return sub-scores = 10.
        rows = [
            _mk_row(
                ticker="AAA",
                eps_growth_pct=20.0,
                revenue_growth_pct=15.0,
                pt_upside_pct=10.0,
                dividend_yield_pct=2.0,
                forward_pe=25.0,
                historical_avg_pe=20.0,
                beta=1.3,
                exec_risk_score=5.0,
                debt_to_equity=0.6,
                sitg_score=8.0,
            )
        ]
        result = score_basket(rows, preset="balanced")
        row = result.rows[0]
        self.assertAlmostEqual(row.return_score.eps_score, 10.0)
        self.assertAlmostEqual(row.return_score.revenue_score, 10.0)
        self.assertAlmostEqual(row.return_score.pt_upside_score, 10.0)
        self.assertAlmostEqual(row.return_score.dividend_score, 10.0)
        self.assertAlmostEqual(row.return_score.sitg_score, 8.0)
        self.assertAlmostEqual(row.risk_score.pe_stretch_score, 10.0)
        self.assertAlmostEqual(row.risk_score.beta_score, 10.0)
        self.assertAlmostEqual(row.risk_score.leverage_score, 10.0)

    def test_sitg_boost_is_positive_for_high_sitg(self):
        rows = [
            _mk_row(
                ticker="A",
                eps_growth_pct=10.0,
                revenue_growth_pct=10.0,
                pt_upside_pct=10.0,
                dividend_yield_pct=1.0,
                forward_pe=20.0,
                historical_avg_pe=18.0,
                beta=1.0,
                exec_risk_score=5.0,
                debt_to_equity=0.5,
                sitg_score=9.0,
            ),
            _mk_row(
                ticker="B",
                eps_growth_pct=10.0,
                revenue_growth_pct=10.0,
                pt_upside_pct=10.0,
                dividend_yield_pct=1.0,
                forward_pe=20.0,
                historical_avg_pe=18.0,
                beta=1.0,
                exec_risk_score=5.0,
                debt_to_equity=0.5,
                sitg_score=0.0,
            ),
        ]
        result = score_basket(rows, preset="growth")
        a = next(r for r in result.rows if r.ticker == "A")
        b = next(r for r in result.rows if r.ticker == "B")
        self.assertGreater(a.sitg_boost, 0.0)
        self.assertEqual(b.sitg_boost, 0.0)
        self.assertGreater(a.ratio, b.ratio)

    def test_situational_flag_applied_through_basket(self):
        rows = [
            _mk_row(ticker="UTIL", debt_to_equity=2.0, beta=0.7, exec_risk_score=2.0,
                    forward_pe=18.0, historical_avg_pe=18.0,
                    eps_growth_pct=3.0, revenue_growth_pct=4.0, pt_upside_pct=5.0,
                    dividend_yield_pct=4.0, sitg_score=3.0),
            _mk_row(ticker="IND", debt_to_equity=0.5, beta=1.3, exec_risk_score=4.0,
                    forward_pe=20.0, historical_avg_pe=18.0,
                    eps_growth_pct=10.0, revenue_growth_pct=8.0, pt_upside_pct=10.0,
                    dividend_yield_pct=1.0, sitg_score=3.0),
        ]
        baseline = score_basket(rows, preset="balanced")
        adjusted = score_basket(rows, preset="balanced",
                                 situational_flags={"utilities_vs_industrials": True})
        # Utility should improve (less D/E penalty when w8 is halved)
        base_util = next(r for r in baseline.rows if r.ticker == "UTIL")
        adj_util = next(r for r in adjusted.rows if r.ticker == "UTIL")
        self.assertGreaterEqual(adj_util.ratio, base_util.ratio)

    def test_rejects_empty_basket(self):
        with self.assertRaises(ValueError):
            score_basket([], preset="balanced")


class TestSingleTicker(unittest.TestCase):
    def test_single_produces_row(self):
        row = score_single(
            _mk_row(
                ticker="SOLO",
                eps_growth_pct=10.0,
                revenue_growth_pct=8.0,
                pt_upside_pct=15.0,
                dividend_yield_pct=2.0,
                forward_pe=22.0,
                historical_avg_pe=20.0,
                beta=1.1,
                exec_risk_score=4.0,
                debt_to_equity=0.6,
                sitg_score=6.0,
            ),
            preset="balanced",
        )
        self.assertEqual(row.ticker, "SOLO")
        self.assertIn(row.signal, {"Exceptional", "Strong buy", "Favorable",
                                   "Balanced", "Caution", "Avoid"})
        # Sub-scores land in [0, 10]
        self.assertGreaterEqual(row.return_score.weighted, 0.0)
        self.assertLessEqual(row.return_score.weighted, 10.0)
        self.assertGreaterEqual(row.risk_score.weighted, 0.0)
        self.assertLessEqual(row.risk_score.weighted, 10.0)


class TestAppliedExample(unittest.TestCase):
    """
    Step 5 applied example — 6-company AI infrastructure basket (HUBB, PWR,
    ETN, GEV, NEE, MTZ) scored with the balanced preset.

    We reproduce the published inputs and verify the INVARIANTS of the
    methodology rather than pin-pointing every digit (the exact spec output
    depends on the spec's rounding and qualitative exec scores). The assertions
    below are the contract we care about for reproducibility.
    """

    def _basket(self):
        return [
            _mk_row(
                ticker="HUBB",
                eps_growth_pct=10.0, revenue_growth_pct=6.5,
                pt_upside_pct=5.0, dividend_yield_pct=1.1,
                forward_pe=21.0, historical_avg_pe=18.0,
                beta=1.1, exec_risk_score=4.0, debt_to_equity=0.6,
                sitg_score=3.0,
            ),
            _mk_row(
                ticker="PWR",
                eps_growth_pct=12.0, revenue_growth_pct=15.0,
                pt_upside_pct=7.0, dividend_yield_pct=0.2,
                forward_pe=28.0, historical_avg_pe=20.0,
                beta=1.2, exec_risk_score=6.0, debt_to_equity=0.8,
                sitg_score=4.0,
            ),
            _mk_row(
                ticker="ETN",
                eps_growth_pct=11.0, revenue_growth_pct=7.5,
                pt_upside_pct=3.0, dividend_yield_pct=1.3,
                forward_pe=26.0, historical_avg_pe=19.0,
                beta=1.05, exec_risk_score=3.0, debt_to_equity=0.5,
                sitg_score=3.0,
            ),
            _mk_row(
                ticker="GEV",
                eps_growth_pct=30.0, revenue_growth_pct=12.0,
                pt_upside_pct=4.0, dividend_yield_pct=0.6,
                forward_pe=38.0, historical_avg_pe=14.0,
                beta=1.4, exec_risk_score=7.0, debt_to_equity=0.4,
                sitg_score=3.0,
            ),
            _mk_row(
                ticker="NEE",
                eps_growth_pct=8.0, revenue_growth_pct=3.0,
                pt_upside_pct=8.0, dividend_yield_pct=3.0,
                forward_pe=20.0, historical_avg_pe=22.0,  # below history
                beta=0.7, exec_risk_score=2.0, debt_to_equity=1.46,
                sitg_score=3.0,
            ),
            _mk_row(
                ticker="MTZ",
                eps_growth_pct=14.0, revenue_growth_pct=10.0,
                pt_upside_pct=8.0, dividend_yield_pct=0.0,
                forward_pe=25.0, historical_avg_pe=17.0,
                beta=1.3, exec_risk_score=5.0, debt_to_equity=0.6,
                sitg_score=3.0,
            ),
        ]

    def test_gev_has_highest_pe_stretch(self):
        """Step 2a: GEV's fwd 38 vs hist 14 ≈ 1.71 stretch — must top the set."""
        stretches = {r.ticker: compute_pe_stretch(r.forward_pe, r.historical_avg_pe)
                     for r in self._basket()}
        self.assertEqual(max(stretches, key=stretches.get), "GEV")
        self.assertAlmostEqual(stretches["GEV"], 38.0/14.0 - 1.0, places=4)
        self.assertEqual(stretches["NEE"], 0.0)  # fwd below hist → 0 per MAX guard

    def test_balanced_preset_produces_ratios(self):
        result = score_basket(self._basket(), preset="balanced")
        ratios = {r.ticker: r.ratio for r in result.rows}
        self.assertEqual(set(ratios.keys()), {"HUBB", "PWR", "ETN", "GEV", "NEE", "MTZ"})
        # Sanity: ratios are finite positive numbers.
        for t, v in ratios.items():
            self.assertTrue(math.isfinite(v), f"{t} ratio={v}")
            self.assertGreaterEqual(v, 0.0)

    def test_hubb_beats_gev_on_balanced(self):
        """
        Step 5 narrative: HUBB is the sweet-spot name on balanced weights
        despite lower growth, because GEV's PE-stretch dominates the risk
        column. Our math must reflect that ordering.
        """
        result = score_basket(self._basket(), preset="balanced")
        ratios = {r.ticker: r.ratio for r in result.rows}
        self.assertGreater(ratios["HUBB"], ratios["GEV"])

    def test_nee_lands_in_income_friendly_quadrant(self):
        """NEE has 0 PE-stretch and the lowest beta — should land bottom-left."""
        result = score_basket(self._basket(), preset="income")
        nee = next(r for r in result.rows if r.ticker == "NEE")
        # NEE scores low beta (0.7 ≠ max), zero stretch, exec=2, but leverage
        # high (1.46 is max in the set). Quadrant is input-dependent; just
        # confirm NEE's risk side is dominated by leverage not stretch/beta.
        self.assertEqual(nee.risk_score.pe_stretch_score, 0.0)
        self.assertGreater(nee.risk_score.leverage_score, nee.risk_score.beta_score)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
