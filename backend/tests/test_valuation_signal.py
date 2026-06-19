"""Unit tests for consensus valuation gap and signal labels."""
import unittest

from backend.valuation_signal import (
    case_assessments,
    implied_downside_pct,
    margin_of_safety_pct,
    valuation_confidence_label,
    valuation_gap_pct,
    valuation_signal_label,
)


class TestValuationSignal(unittest.TestCase):
    def test_gap_and_downside_user_example(self):
        price = 367.46
        fair = 273.0
        gap = valuation_gap_pct(price, fair)
        downside = implied_downside_pct(price, fair)
        mos = margin_of_safety_pct(price, fair)
        self.assertAlmostEqual(gap, 34.6, places=1)
        self.assertAlmostEqual(downside, -25.7, places=1)
        self.assertAlmostEqual(mos, -34.6, places=1)
        self.assertAlmostEqual(gap, -mos, places=1)

    def test_moderately_overvalued_when_bull_near_spot(self):
        gap = valuation_gap_pct(367.46, 273.0)
        signal = valuation_signal_label(gap, 367.46, 355.0)
        self.assertEqual(signal, "Moderately Overvalued")

    def test_bull_bear_case_labels(self):
        bull, bear = case_assessments(367.46, 210.0, 355.0)
        self.assertEqual(bull, "Near fair value")
        self.assertEqual(bear, "Significantly overvalued")

    def test_confidence_medium_with_two_models(self):
        label = valuation_confidence_label(
            2,
            True,
            210.0,
            355.0,
            267.0,
            [267.0, 278.0],
        )
        self.assertIn(label, ("Medium", "High"))

    def test_near_fair_value_band(self):
        self.assertEqual(valuation_signal_label(3.0, 100.0, 95.0), "Near Fair Value")


if __name__ == "__main__":
    unittest.main()
