import unittest

from backend.predictor.calibration import (
    calibration_band,
    empirical_coverage_fraction,
    q10_q90_hit,
)


class TestCalibration(unittest.TestCase):
    def test_interval_hit(self) -> None:
        self.assertTrue(q10_q90_hit(100.0, 90.0, 110.0))
        self.assertFalse(q10_q90_hit(120.0, 90.0, 110.0))

    def test_band_defaults(self) -> None:
        lo, hi = calibration_band()
        self.assertLess(lo, hi)


if __name__ == "__main__":
    unittest.main()
