"""Unit tests for backend.metric_primitives."""
import unittest

from backend.metric_primitives import (
    fcf_yield_decimal,
    fcf_yield_percent,
    format_usd_compact,
    graham_fair_value,
    normalize_gross_margin,
    roic_proxy,
    verdict_tone,
)


class TestMetricPrimitives(unittest.TestCase):
    def test_roic_proxy(self):
        self.assertEqual(roic_proxy(20.0), 16.0)
        self.assertEqual(roic_proxy(0.0), 0.0)
        self.assertEqual(roic_proxy(-5.0), 0.0)

    def test_fcf_yield(self):
        self.assertAlmostEqual(fcf_yield_decimal(4_000_000_000, 100_000_000_000), 0.04)
        self.assertAlmostEqual(fcf_yield_percent(4_000_000_000, 100_000_000_000), 4.0)
        self.assertIsNone(fcf_yield_decimal(None, 100))
        self.assertIsNone(fcf_yield_decimal(100, 0))

    def test_normalize_gross_margin_ratio(self):
        gm = normalize_gross_margin(0.42)
        self.assertIsNotNone(gm)
        assert gm is not None
        self.assertAlmostEqual(gm.ratio, 0.42)
        self.assertAlmostEqual(gm.percent, 42.0)

    def test_normalize_gross_margin_percent(self):
        gm = normalize_gross_margin(42.0)
        self.assertIsNotNone(gm)
        assert gm is not None
        self.assertAlmostEqual(gm.ratio, 0.42)
        self.assertAlmostEqual(gm.percent, 42.0)

    def test_format_usd_compact(self):
        self.assertEqual(format_usd_compact(None), "N/A")
        self.assertEqual(format_usd_compact(1_500_000_000), "$1.50B")
        self.assertEqual(format_usd_compact(2_500_000), "$2.50M")

    def test_graham_fair_value(self):
        v = graham_fair_value(5.0, 20.0)
        self.assertIsNotNone(v)
        self.assertAlmostEqual(v, (22.5 * 5.0 * 20.0) ** 0.5)
        self.assertIsNone(graham_fair_value(0, 20))

    def test_verdict_tone(self):
        self.assertEqual(verdict_tone("STRONG BUY"), "strong_positive")
        self.assertEqual(verdict_tone("BUY"), "positive")
        self.assertEqual(verdict_tone("NEUTRAL"), "neutral")
        self.assertEqual(verdict_tone("Caution"), "caution")
        self.assertEqual(verdict_tone("SELL"), "negative")


if __name__ == "__main__":
    unittest.main()
