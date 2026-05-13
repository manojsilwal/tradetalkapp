import unittest

import pandas as pd

from backend.macro_flow.macro_flow_agent import chaikin_money_flow


class TestMacroFlowCmf(unittest.TestCase):
    def test_cmf_positive_on_persistent_buy_pressure(self):
        n = 30
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
        high = close + 0.01
        low = close - 0.5
        open_ = close - 0.05
        vol = pd.Series([1e6] * n, index=idx)
        df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol})
        v = chaikin_money_flow(df, n=21)
        self.assertGreater(v, 0.0)

    def test_cmf_zero_on_empty(self):
        self.assertEqual(chaikin_money_flow(pd.DataFrame(), n=21), 0.0)


if __name__ == "__main__":
    unittest.main()
