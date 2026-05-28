import unittest

from backend.predictor.calibration import (
    calibration_band,
    empirical_coverage_fraction,
    interval_pinball_mean,
    pinball_loss,
    q10_q90_hit,
)


class TestCalibration(unittest.TestCase):
    def test_interval_hit(self) -> None:
        self.assertTrue(q10_q90_hit(100.0, 90.0, 110.0))
        self.assertFalse(q10_q90_hit(120.0, 90.0, 110.0))

    def test_band_defaults(self) -> None:
        lo, hi = calibration_band()
        self.assertLess(lo, hi)

    def test_pinball_loss_asymmetric(self) -> None:
        self.assertAlmostEqual(pinball_loss(110.0, 100.0, 0.5), 5.0)
        self.assertAlmostEqual(pinball_loss(90.0, 100.0, 0.5), 5.0)

    def test_interval_pinball_mean(self) -> None:
        val = interval_pinball_mean(100.0, 90.0, 100.0, 110.0)
        self.assertGreater(val, 0.0)


if __name__ == "__main__":
    unittest.main()
