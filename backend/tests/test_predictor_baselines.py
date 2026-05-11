import unittest

import numpy as np
from hypothesis import given, settings, strategies as st

from backend.predictor.baselines import (
    drift_forecast,
    ewma_forecast,
    naive_forecast,
    seasonal_naive_forecast,
)


class TestBaselines(unittest.TestCase):
    @settings(max_examples=40, deadline=None)
    @given(st.lists(st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False), min_size=10, max_size=120))
    def test_baselines_finite(self, vals: list[float]) -> None:
        s = np.array(vals, dtype=np.float64)
        h = 5
        for fn in (naive_forecast, seasonal_naive_forecast, ewma_forecast, drift_forecast):
            x = fn(s, h)
            self.assertFalse(np.isnan(x))
            self.assertTrue(np.isfinite(x))


if __name__ == "__main__":
    unittest.main()
