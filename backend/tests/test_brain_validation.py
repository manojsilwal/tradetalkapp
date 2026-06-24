"""Validation: purged/embargoed split removes leakage; metrics are correct."""
import unittest

import numpy as np

from backend.brain import validation as val


class TestValidation(unittest.TestCase):
    def test_purged_split_excludes_overlapping_train(self):
        # 10 samples at days 0..9, horizon 3, test = days [6, 9].
        dates = np.arange(10)
        train, test = val.purged_time_split(dates, test_start=6, test_end=9,
                                            horizon_days=3, embargo=0)
        self.assertEqual(set(test.tolist()), {6, 7, 8, 9})
        # A train sample at day d sees up to d+3. d=3 -> 6 overlaps test start.
        # So days 3,4,5 must be purged; only 0,1,2 remain.
        self.assertEqual(set(train.tolist()), {0, 1, 2})

    def test_embargo_widens_purge(self):
        dates = np.arange(20)
        train, test = val.purged_time_split(dates, 10, 14, horizon_days=2, embargo=2)
        train_set = set(train.tolist())
        # test window itself excluded
        for d in (10, 11, 12, 13, 14):
            self.assertNotIn(d, train_set)
        # day 9 sees up to 11 -> overlaps embargoed band [8, 16] -> purged
        self.assertNotIn(9, train_set)
        # day 5 sees up to 7 (< 8) -> safe, kept
        self.assertIn(5, train_set)
        # day 15 within embargo band -> purged; day 17 beyond it -> kept
        self.assertNotIn(15, train_set)
        self.assertIn(17, train_set)

    def test_roc_auc_perfect_and_random(self):
        y = np.array([0, 0, 1, 1])
        self.assertAlmostEqual(val.roc_auc(y, np.array([0.1, 0.2, 0.8, 0.9])), 1.0)
        self.assertAlmostEqual(val.roc_auc(y, np.array([0.9, 0.8, 0.2, 0.1])), 0.0)
        # all-one-class -> degenerate 0.5
        self.assertEqual(val.roc_auc(np.array([1, 1]), np.array([0.5, 0.6])), 0.5)

    def test_brier_and_accuracy(self):
        y = np.array([0, 1])
        self.assertAlmostEqual(val.brier_score(y, np.array([0.0, 1.0])), 0.0)
        self.assertAlmostEqual(val.accuracy(y, np.array([0.4, 0.6])), 1.0)

    def test_precision_at_k(self):
        y = np.array([0, 1, 1, 0])
        scores = np.array([0.1, 0.9, 0.8, 0.2])
        self.assertAlmostEqual(val.precision_at_k(y, scores, 2), 1.0)

    def test_classification_report_keys(self):
        rng = np.random.default_rng(0)
        y = rng.integers(0, 2, size=50)
        p = rng.uniform(size=50)
        rep = val.classification_report(y, p, k=10)
        for key in ("auc", "brier", "accuracy", "precision_at_k", "n", "base_rate"):
            self.assertIn(key, rep)


if __name__ == "__main__":
    unittest.main()
