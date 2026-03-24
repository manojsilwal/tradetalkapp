"""Unit tests for deterministic gold technicals (no network)."""
import unittest
import pandas as pd
import numpy as np

from backend.gold_technicals import compute_gold_technicals


class TestGoldTechnicals(unittest.TestCase):
    def test_synthetic_ohlc_returns_structure(self):
        n = 220
        rng = np.random.default_rng(42)
        base = 2000 + np.cumsum(rng.normal(0, 8, size=n))
        df = pd.DataFrame(
            {
                "Open": base + rng.normal(0, 2, n),
                "High": base + np.abs(rng.normal(2, 2, n)),
                "Low": base - np.abs(rng.normal(2, 2, n)),
                "Close": base,
            }
        )
        out = compute_gold_technicals(df)
        self.assertNotIn("error", out)
        self.assertEqual(out["bars_used"], n)
        self.assertIn("rsi_14", out)
        self.assertIn("classic_pivots", out)
        self.assertIn("pivot", out["classic_pivots"])


if __name__ == "__main__":
    unittest.main()
