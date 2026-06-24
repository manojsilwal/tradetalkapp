"""Forward outperformance label tests."""
import unittest

from backend.brain import labels


class TestLabels(unittest.TestCase):
    def test_forward_label_outperforms(self):
        # stock +20%, benchmark +10% over the horizon -> outperformed
        prices = [100] * 1 + [100, 120]
        bench = [100] * 1 + [100, 110]
        lab = labels.forward_label(prices, bench, t=1, horizon_days=1)
        self.assertIsNotNone(lab)
        self.assertTrue(lab["outperformed_benchmark"])
        self.assertAlmostEqual(lab["future_stock_return"], 0.20)
        self.assertAlmostEqual(lab["future_benchmark_return"], 0.10)
        self.assertAlmostEqual(lab["future_excess_return"], 0.10)

    def test_forward_label_underperforms(self):
        prices = [100, 105]
        bench = [100, 115]
        lab = labels.forward_label(prices, bench, t=0, horizon_days=1)
        self.assertFalse(lab["outperformed_benchmark"])
        self.assertLess(lab["future_excess_return"], 0)

    def test_incomplete_window_returns_none(self):
        prices = [100, 101, 102]
        bench = [100, 101, 102]
        # horizon runs off the end -> no label (never label on partial future)
        self.assertIsNone(labels.forward_label(prices, bench, t=2, horizon_days=5))

    def test_mismatched_lengths_returns_none(self):
        self.assertIsNone(labels.forward_label([1, 2, 3], [1, 2], t=0, horizon_days=1))

    def test_build_labels_panel_alignment(self):
        prices = list(range(100, 200))
        bench = list(range(100, 200))
        panel = [
            {"ticker": "A", "as_of_idx": 10, "prices": prices, "benchmark": bench},
            {"ticker": "A", "as_of_idx": 98, "prices": prices, "benchmark": bench},
        ]
        labs = labels.build_labels_panel(panel, horizon_days=5)
        self.assertEqual(len(labs), 2)
        self.assertIsNotNone(labs[0])
        self.assertIsNone(labs[1])  # 98 + 5 >= 100 -> incomplete


if __name__ == "__main__":
    unittest.main()
