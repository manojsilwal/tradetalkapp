"""Historical replay calibration — coverage and pinball vs realized forward close."""

import os
import unittest

from backend.predictor.calibration import empirical_coverage_fraction, q10_q90_hit
from backend.predictor.eval.historical_calibration import (
    evaluate_replay_row,
    load_close_series,
    run_historical_calibration,
)


class TestHistoricalCalibration(unittest.TestCase):
    def test_q10_q90_hit_golden(self) -> None:
        self.assertTrue(q10_q90_hit(100.0, 90.0, 110.0))
        self.assertFalse(q10_q90_hit(120.0, 90.0, 110.0))

    def test_evaluate_replay_row_synthetic(self) -> None:
        series, src = load_close_series("AAPL")
        row = evaluate_replay_row(
            ticker="AAPL",
            as_of="2018-12-14",
            horizon="21d",
            series=series,
            price_source=src,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.q10, min(row.q10, row.q50, row.q90))
        self.assertEqual(row.q90, max(row.q10, row.q50, row.q90))
        self.assertGreater(row.pinball, 0.0)
        self.assertGreater(row.realized, 0.0)

    def test_run_historical_calibration_on_corpus(self) -> None:
        os.environ["PREDICTOR_ENABLE"] = "1"
        os.environ["PREDICTOR_USE_DATA_LAKE"] = "0"
        out = run_historical_calibration(limit=20)
        self.assertGreaterEqual(out.get("evaluated", 0), 10, msg=out)
        self.assertIn("coverage", out)
        self.assertIn("mean_pinball", out)
        self.assertIn("pinball_ratio_vs_naive", out)
        cov = float(out["coverage"])
        self.assertGreaterEqual(cov, 0.0)
        self.assertLessEqual(cov, 1.0)
        hits = [True, True, False, True]
        self.assertAlmostEqual(empirical_coverage_fraction(hits), 0.75)

    def test_calibration_gate_passes_offline_corpus(self) -> None:
        os.environ["PREDICTOR_ENABLE"] = "1"
        os.environ["PREDICTOR_USE_DATA_LAKE"] = "0"
        out = run_historical_calibration(limit=50)
        self.assertTrue(
            out.get("ok"),
            msg=(
                f"coverage={out.get('coverage')} "
                f"pinball_ratio={out.get('pinball_ratio_vs_naive')} "
                f"evaluated={out.get('evaluated')}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
