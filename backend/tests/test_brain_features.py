"""Feature engineering tests, including the no-lookahead guarantee."""
import unittest

import numpy as np

from backend.brain import FEATURE_LIST
from backend.brain import dataset, features


class TestFeatures(unittest.TestCase):
    def setUp(self):
        self.prices = list(dataset.make_price_series(n=300, seed=1))
        self.bench = list(dataset.make_price_series(n=300, seed=2))

    def test_feature_row_has_full_contract(self):
        row = features.build_feature_row(self.prices, self.bench, fundamentals={})
        self.assertEqual(set(row.keys()), set(FEATURE_LIST))

    def test_momentum_values_present_with_enough_history(self):
        m = features.momentum_features(self.prices, self.bench)
        for k in ("return_1m", "return_3m", "return_6m", "return_12m"):
            self.assertIsNotNone(m[k])
        self.assertIsNotNone(m["relative_strength_3m"])
        self.assertIsNotNone(m["price_vs_200dma"])

    def test_short_history_returns_none_not_fabricated(self):
        short = self.prices[:10]
        m = features.momentum_features(short, None)
        self.assertIsNone(m["return_3m"])
        self.assertIsNone(m["relative_strength_3m"])

    def test_fundamentals_passthrough(self):
        fund = {"roic": 0.2, "pe_ratio": 18.0, "sentiment_score": 0.3}
        row = features.build_feature_row(self.prices, self.bench, fundamentals=fund)
        self.assertAlmostEqual(row["roic"], 0.2)
        self.assertAlmostEqual(row["pe_ratio"], 18.0)

    def test_no_lookahead_mutating_future_does_not_change_past_row(self):
        """The headline guarantee: a feature row at index t must not depend on
        any data after t. Build at t, then corrupt the future, rebuild, compare."""
        as_of = 200
        panel = [{
            "ticker": "AAA", "date": as_of, "as_of_idx": as_of,
            "prices": list(self.prices), "benchmark": list(self.bench),
            "fundamentals": {},
        }]
        before = features.build_features_panel(panel)[0]

        corrupted = list(self.prices)
        for i in range(as_of + 1, len(corrupted)):
            corrupted[i] = corrupted[i] * 100.0  # wild future
        panel[0]["prices"] = corrupted
        after = features.build_features_panel(panel)[0]

        for k in FEATURE_LIST:
            self.assertEqual(before[k], after[k], msg=f"lookahead leak in {k}")

    def test_point_in_time_slice(self):
        s = features.point_in_time_slice([10, 11, 12, 13], 1)
        self.assertEqual(s, [10, 11])


if __name__ == "__main__":
    unittest.main()
