"""Tests for sync_prices_to_bq parquet type normalization."""
from __future__ import annotations

import tempfile
import unittest

import pandas as pd


class TestSyncPricesNormalize(unittest.TestCase):
    def test_normalize_price_df_uses_timestamp_not_string(self):
        from scripts.sync_prices_to_bq import normalize_price_df

        raw = pd.DataFrame(
            {
                "Close": [100.0, 101.0],
                "Volume": [1000.0, 2000.0],
                "daily_return_pct": [0.1, 0.2],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        raw.index.name = "Date"

        norm = normalize_price_df(raw, "CAT")
        self.assertEqual(norm["symbol"].iloc[0], "CAT")
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(norm["trade_date"]))
        self.assertTrue(
            pd.api.types.is_datetime64_any_dtype(norm["ingested_at"])
            or hasattr(norm["ingested_at"].dtype, "tz")
        )
        self.assertNotEqual(norm["ingested_at"].dtype, object)

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            from scripts.sync_prices_to_bq import write_bq_parquet

            write_bq_parquet(norm, tmp.name)
            import pyarrow.parquet as pq

            schema = pq.read_schema(tmp.name)
            ingested_field = schema.field("ingested_at")
            trade_field = schema.field("trade_date")
            self.assertIn("timestamp", str(ingested_field.type))
            self.assertTrue(str(trade_field.type).startswith("date32"))


if __name__ == "__main__":
    unittest.main()
