"""
Tests for Phase C1 PR 2 — pure tool handlers.

These must be DETERMINISTIC, side-effect free, and byte-exactly reproduce the
pre-refactor decision boundaries for the three tier-0 tools. Any drift here
is a bug.
"""
from __future__ import annotations

import unittest

from backend.tool_handlers import (
    TOOL_HANDLERS,
    classify_short_interest,
    decide_debate_bear_stance,
    decide_debate_bull_stance,
    verify_short_interest,
    vix_to_credit_stress_status,
    vix_to_credit_stress_value,
)


_MACRO_DEFAULT = {
    "divisor": 15.0,
    "status_threshold": 1.1,
}


_SIR_DEFAULT = {
    "sir_bull_threshold": 15.0,
    "sir_ambiguous_min": 10.0,
    "sir_ambiguous_max": 20.0,
    "dtc_confirm_threshold": 5.0,
    "bearish_csi_threshold": 1.1,
}

_BULL_DEFAULT = {
    "sir_bull_floor": 5.0,
    "rev_growth_bull_floor": 15.0,
    "r3m_bull_floor": 5.0,
    "sir_bear_ceiling": 2.0,
    "rev_growth_bear_ceiling": 0.0,
    "r3m_bear_ceiling": -10.0,
}

_BEAR_DEFAULT = {
    "pe_bear_threshold": 50.0,
    "debt_eq_bear_threshold": 200.0,
    "r3m_bear_ceiling": -15.0,
    "pe_bull_ceiling": 20.0,
    "r3m_bull_floor": 0.0,
}


class TestShortInterestClassifier(unittest.TestCase):
    def test_initial_sir_above_threshold_returns_1(self):
        out = classify_short_interest(
            {"short_interest_ratio": 18, "days_to_cover": 1}, _SIR_DEFAULT, revision=False
        )
        self.assertEqual(out, 1)

    def test_initial_sir_at_threshold_is_not_strictly_bull(self):
        out = classify_short_interest(
            {"short_interest_ratio": 15.0, "days_to_cover": 1}, _SIR_DEFAULT, revision=False
        )
        self.assertEqual(out, -1)

    def test_initial_in_ambiguous_band_returns_minus1(self):
        out = classify_short_interest(
            {"short_interest_ratio": 12, "days_to_cover": 1}, _SIR_DEFAULT, revision=False
        )
        self.assertEqual(out, -1)

    def test_initial_below_ambiguous_band_returns_0(self):
        out = classify_short_interest(
            {"short_interest_ratio": 5, "days_to_cover": 1}, _SIR_DEFAULT, revision=False
        )
        self.assertEqual(out, 0)

    def test_revision_requires_both_sir_and_dtc(self):
        self.assertEqual(
            classify_short_interest(
                {"short_interest_ratio": 18, "days_to_cover": 6},
                _SIR_DEFAULT, revision=True,
            ), 1,
        )
        self.assertEqual(
            classify_short_interest(
                {"short_interest_ratio": 18, "days_to_cover": 3},
                _SIR_DEFAULT, revision=True,
            ), 0,
        )
        self.assertEqual(
            classify_short_interest(
                {"short_interest_ratio": 12, "days_to_cover": 8},
                _SIR_DEFAULT, revision=True,
            ), 0,
        )

    def test_missing_keys_default_to_zero(self):
        self.assertEqual(classify_short_interest({}, _SIR_DEFAULT, revision=False), 0)
        self.assertEqual(classify_short_interest({}, _SIR_DEFAULT, revision=True), 0)

    def test_none_values_default_to_zero(self):
        self.assertEqual(
            classify_short_interest(
                {"short_interest_ratio": None, "days_to_cover": None},
                _SIR_DEFAULT, revision=False,
            ), 0,
        )

    def test_verify_short_interest_respects_csi_threshold(self):
        self.assertTrue(verify_short_interest({"credit_stress_index": 1.0}, _SIR_DEFAULT))
        self.assertTrue(verify_short_interest({"credit_stress_index": 1.1}, _SIR_DEFAULT))
        self.assertFalse(verify_short_interest({"credit_stress_index": 1.2}, _SIR_DEFAULT))


class TestDebateBullStance(unittest.TestCase):
    def test_bullish_when_sir_above_floor(self):
        self.assertEqual(
            decide_debate_bull_stance(
                {"short_interest_ratio": 6, "revenue_growth": 0, "price_return_3m": 0},
                _BULL_DEFAULT,
            ), "BULLISH",
        )

    def test_bullish_when_revenue_growth_above_floor(self):
        self.assertEqual(
            decide_debate_bull_stance(
                {"short_interest_ratio": 0, "revenue_growth": 16, "price_return_3m": 0},
                _BULL_DEFAULT,
            ), "BULLISH",
        )

    def test_bearish_when_all_ceilings_broken(self):
        self.assertEqual(
            decide_debate_bull_stance(
                {"short_interest_ratio": 1, "revenue_growth": -5, "price_return_3m": -15},
                _BULL_DEFAULT,
            ), "BEARISH",
        )

    def test_neutral_otherwise(self):
        self.assertEqual(
            decide_debate_bull_stance(
                {"short_interest_ratio": 3, "revenue_growth": 5, "price_return_3m": 0},
                _BULL_DEFAULT,
            ), "NEUTRAL",
        )


class TestDebateBearStance(unittest.TestCase):
    def test_bearish_when_high_pe(self):
        self.assertEqual(
            decide_debate_bear_stance(
                {"pe_ratio": 60, "debt_to_equity": 0, "price_return_3m": 0},
                _BEAR_DEFAULT,
            ), "BEARISH",
        )

    def test_bearish_when_high_debt_equity(self):
        self.assertEqual(
            decide_debate_bear_stance(
                {"pe_ratio": 30, "debt_to_equity": 300, "price_return_3m": 0},
                _BEAR_DEFAULT,
            ), "BEARISH",
        )

    def test_bearish_on_deep_drawdown(self):
        self.assertEqual(
            decide_debate_bear_stance(
                {"pe_ratio": 10, "debt_to_equity": 10, "price_return_3m": -20},
                _BEAR_DEFAULT,
            ), "BEARISH",
        )

    def test_bullish_on_low_pe_and_positive_return(self):
        self.assertEqual(
            decide_debate_bear_stance(
                {"pe_ratio": 15, "debt_to_equity": 10, "price_return_3m": 5},
                _BEAR_DEFAULT,
            ), "BULLISH",
        )

    def test_neutral_fallback(self):
        self.assertEqual(
            decide_debate_bear_stance(
                {"pe_ratio": 25, "debt_to_equity": 50, "price_return_3m": -5},
                _BEAR_DEFAULT,
            ), "NEUTRAL",
        )


class TestVixToCreditStress(unittest.TestCase):
    def test_status_normal_when_below_threshold(self):
        self.assertEqual(
            vix_to_credit_stress_status({"vix_level": 15.0}, _MACRO_DEFAULT),
            "NORMAL",
        )

    def test_status_stress_when_above_threshold(self):
        self.assertEqual(
            vix_to_credit_stress_status({"vix_level": 30.0}, _MACRO_DEFAULT),
            "STRESS",
        )

    def test_status_normal_at_exact_boundary(self):
        # csi == threshold is NOT stress (strict greater-than).
        self.assertEqual(
            vix_to_credit_stress_status({"vix_level": 16.5}, _MACRO_DEFAULT),
            "NORMAL",
        )

    def test_status_invalid_on_zero_divisor(self):
        self.assertEqual(
            vix_to_credit_stress_status(
                {"vix_level": 20.0}, {"divisor": 0.0, "status_threshold": 1.0},
            ),
            "INVALID",
        )

    def test_status_invalid_on_missing_keys(self):
        self.assertEqual(
            vix_to_credit_stress_status({"vix_level": 20.0}, {}),
            "INVALID",
        )

    def test_status_handles_missing_vix(self):
        # Absent vix → 0 → NORMAL, never crashes.
        self.assertEqual(
            vix_to_credit_stress_status({}, _MACRO_DEFAULT),
            "NORMAL",
        )

    def test_value_preserves_legacy_formula(self):
        # Pre-evolution production shipped round(vix/15.0, 2). The handler
        # under default config must match that exactly.
        for vix in (0.0, 9.2, 15.0, 16.5, 30.0, 82.7):
            self.assertEqual(
                vix_to_credit_stress_value({"vix_level": vix}, _MACRO_DEFAULT),
                round(vix / 15.0, 2),
            )

    def test_value_respects_custom_divisor(self):
        self.assertEqual(
            vix_to_credit_stress_value(
                {"vix_level": 20.0}, {"divisor": 10.0, "status_threshold": 1.0},
            ),
            2.0,
        )

    def test_value_falls_back_on_bad_divisor(self):
        # Production must never divide by zero.
        self.assertEqual(
            vix_to_credit_stress_value(
                {"vix_level": 30.0}, {"divisor": 0.0, "status_threshold": 1.0},
            ),
            2.0,  # 30/15 fallback
        )


class TestToolHandlersRegistry(unittest.TestCase):
    """The TOOL_HANDLERS lookup is the contract SEPL's shadow evaluator uses."""

    def test_all_tools_are_registered(self):
        for name in (
            "short_interest_classifier",
            "debate_stance_heuristic_bull",
            "debate_stance_heuristic_bear",
            "macro_vix_to_credit_stress",
        ):
            self.assertIn(name, TOOL_HANDLERS)

    def test_every_handler_is_callable_with_empty_data(self):
        for name, entry in TOOL_HANDLERS.items():
            fn = entry["fn"]
            defaults = {
                "short_interest_classifier": _SIR_DEFAULT,
                "debate_stance_heuristic_bull": _BULL_DEFAULT,
                "debate_stance_heuristic_bear": _BEAR_DEFAULT,
                "macro_vix_to_credit_stress": _MACRO_DEFAULT,
            }[name]
            out = fn({}, defaults)
            # No exceptions, result is a primitive.
            self.assertIn(type(out).__name__, ("int", "str"))


if __name__ == "__main__":
    unittest.main()
