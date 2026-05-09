"""Phase E5 — OHLCV normalization contract tests."""
import unittest

from backend.data_lake.ohlcv_normalizer import OHLCVBar, describe_window, normalize_bar


class TestOhlcvNormalizer(unittest.TestCase):
    def test_normalize_bar_outputs_structural_fields(self):
        bar = OHLCVBar(open=100.0, high=103.0, low=99.0, close=102.0, volume=1_200_000)
        out = normalize_bar(bar, prev_close=98.0, volume_window=[900_000, 1_000_000, 1_100_000])
        self.assertIn("open_gap", out)
        self.assertIn("high_range", out)
        self.assertIn("low_range", out)
        self.assertIn("close_body", out)
        self.assertIn("volume_zscore", out)
        self.assertAlmostEqual(out["open_gap"], (100.0 - 98.0) / 98.0, places=6)

    def test_describe_window_is_deterministic(self):
        bars = [
            {"open_gap": 0.01, "high_range": 0.03, "low_range": -0.01, "close_body": 0.02, "volume_zscore": 1.1},
            {"open_gap": -0.002, "high_range": 0.015, "low_range": -0.02, "close_body": -0.005, "volume_zscore": 0.2},
        ]
        a = describe_window(bars)
        b = describe_window(bars)
        self.assertEqual(a, b)
        self.assertIn("volume_z=", a)
        self.assertIn("close_body=", a)


if __name__ == "__main__":
    unittest.main()
