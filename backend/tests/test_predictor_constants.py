import unittest

from backend.predictor.timesfm_constants import IDX_MEAN, IDX_Q10, IDX_Q50, IDX_Q90


class TestPredictorQuantileIndices(unittest.TestCase):
    def test_indices(self) -> None:
        self.assertEqual(IDX_MEAN, 0)
        self.assertEqual(IDX_Q10, 1)
        self.assertEqual(IDX_Q50, 5)
        self.assertEqual(IDX_Q90, 9)


if __name__ == "__main__":
    unittest.main()
