import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd


class TestPitAsOf(unittest.TestCase):
    def test_knowledge_date_filters_future_rows(self) -> None:
        from backend.predictor import pit

        tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
        self.addCleanup(lambda: os.unlink(tmp.name))
        idx = pd.to_datetime(["2019-12-31", "2020-03-31", "2020-06-30"])
        df = pd.DataFrame(
            {
                "roe": [0.10, 0.12, 0.15],
                "knowledge_date": pd.to_datetime(
                    ["2020-02-15", "2020-05-20", "2020-08-10"]
                ),
            },
            index=idx,
        )
        df.to_parquet(tmp.name, index=True)

        with patch.object(pit, "resolve_fundamentals_parquet_path", return_value=tmp.name):
            v = pit.as_of("DUMMY", "roe", "2020-06-01")
            self.assertAlmostEqual(v, 0.12, places=4)

    def test_leh_in_survivorship_list(self) -> None:
        from backend.data_lake.config import HISTORICAL_REMOVED_TICKERS

        self.assertIn("LEH", HISTORICAL_REMOVED_TICKERS)


if __name__ == "__main__":
    unittest.main()
