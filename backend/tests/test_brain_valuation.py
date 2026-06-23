"""DCF / reverse-DCF intrinsic value tests (offline)."""
import unittest

from backend.brain import valuation as val


class TestValuation(unittest.TestCase):
    def test_higher_discount_lowers_value(self):
        cheap = val.dcf_value(10, 0.10, 5, 0.025, 0.08)
        dear = val.dcf_value(10, 0.10, 5, 0.025, 0.12)
        self.assertGreater(cheap, dear)

    def test_higher_growth_raises_value(self):
        low = val.dcf_value(10, 0.05, 5, 0.025, 0.09)
        high = val.dcf_value(10, 0.15, 5, 0.025, 0.09)
        self.assertGreater(high, low)

    def test_discount_must_exceed_terminal(self):
        with self.assertRaises(ValueError):
            val.dcf_value(10, 0.10, 5, 0.05, 0.04)

    def test_intrinsic_range_sorted_and_positive(self):
        rng = val.intrinsic_range(fcf0=8.0, growth=0.10, years=5,
                                  terminal_growth=0.025, discount_rate=0.09)
        lo, mid, hi = (rng["intrinsic_value_low"], rng["intrinsic_value_mid"],
                       rng["intrinsic_value_high"])
        self.assertLessEqual(lo, mid)
        self.assertLessEqual(mid, hi)
        self.assertGreater(lo, 0)

    def test_reverse_dcf_round_trip(self):
        g0 = 0.11
        v = val.dcf_value(7.0, g0, 5, 0.025, 0.09)
        implied = val.reverse_dcf(v, 7.0, years=5, terminal_growth=0.025, discount_rate=0.09)
        self.assertIsNotNone(implied)
        self.assertAlmostEqual(implied, g0, places=3)

    def test_reverse_dcf_unreachable_returns_none(self):
        # An absurdly high target no growth in [-0.5, 1.0] can reach.
        self.assertIsNone(val.reverse_dcf(1e12, 1.0, years=5,
                                          terminal_growth=0.025, discount_rate=0.09))

    def test_dcf_upside(self):
        self.assertAlmostEqual(val.dcf_upside(160, 125), 0.28)
        self.assertIsNone(val.dcf_upside(160, 0))
        self.assertIsNone(val.dcf_upside(None, 100))

    def test_equity_to_ev(self):
        self.assertAlmostEqual(val.equity_to_ev(100, 0, 0), 1.0)
        self.assertAlmostEqual(val.equity_to_ev(100, 100, 0), 0.5)
        self.assertEqual(val.equity_to_ev(0, 0, 0), 1.0)  # degenerate -> 1.0


if __name__ == "__main__":
    unittest.main()
