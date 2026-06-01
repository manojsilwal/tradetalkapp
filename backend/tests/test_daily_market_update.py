"""Unit tests for daily incremental market update helpers."""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

from backend.data_lake.daily_market_update import (
    _frame_to_bq_rows,
    resolve_ingest_window,
    target_through_date,
)


class TestDailyMarketWindow(unittest.TestCase):
    def test_target_through_is_yesterday_et(self):
        with patch("backend.data_lake.daily_market_update.as_et_today") as mock_today:
            mock_today.return_value = date(2026, 5, 31)
            self.assertEqual(target_through_date(), date(2026, 5, 30))

    def test_resolve_window_when_behind(self):
        with patch("backend.data_lake.daily_market_update.get_bq_last_trade_date") as mock_last:
            mock_last.return_value = date(2026, 5, 28)
            window = resolve_ingest_window(through=date(2026, 5, 30))
            self.assertEqual(window, (date(2026, 5, 29), date(2026, 5, 30)))

    def test_resolve_window_when_current(self):
        with patch("backend.data_lake.daily_market_update.get_bq_last_trade_date") as mock_last:
            mock_last.return_value = date(2026, 5, 30)
            self.assertIsNone(resolve_ingest_window(through=date(2026, 5, 30)))


class TestFrameToBqRows(unittest.TestCase):
    def test_filters_to_ingest_start(self):
        df = pd.DataFrame({
            "symbol": ["AAPL", "AAPL"],
            "trade_date": pd.to_datetime(["2026-05-28", "2026-05-29"]),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1000, 1100],
            "daily_return_pct": [0.5, 1.0],
            "ma_20": [100.0, 100.5],
            "ma_50": [99.0, 99.5],
            "ma_200": [95.0, 95.2],
            "relative_volume": [1.0, 1.1],
            "ingested_at": pd.Timestamp.now(tz="UTC"),
        })
        rows = _frame_to_bq_rows(df, ingest_start=date(2026, 5, 29))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_date"], "2026-05-29")
        self.assertEqual(rows[0]["symbol"], "AAPL")


class TestIncrementalMovementLinks(unittest.TestCase):
    def test_incremental_skips_without_bigquery(self):
        from backend.mcp_server.build_movement_links import run_incremental_movement_links

        with patch.dict("os.environ", {"MCP_DATA_BACKEND": "duckdb"}):
            result = run_incremental_movement_links("2026-05-28", "2026-05-30")
        self.assertEqual(result["status"], "skipped")

    def test_incremental_dry_run(self):
        from backend.mcp_server.build_movement_links import run_incremental_movement_links

        with patch.dict("os.environ", {"MCP_DATA_BACKEND": "bigquery"}):
            result = run_incremental_movement_links(
                "2026-05-28", "2026-05-30", dry_run=True
            )
        self.assertEqual(result["status"], "dry_run")


class TestCollectIncrementalEvents(unittest.TestCase):
    def test_collect_incremental_filters_sec_by_date(self):
        from backend.data_lake.ingest_daily_events import collect_incremental_events

        in_window = {
            "event_id": "a",
            "published_at": "2026-05-29T21:00:00+00:00",
            "category": "sec_filing",
            "headline": "8-K",
            "body_text": "",
            "affected_symbols": ["AAPL"],
            "dedupe_cluster_id": "x",
        }
        out_window = {
            **in_window,
            "event_id": "b",
            "published_at": "2026-01-01T21:00:00+00:00",
        }
        with patch(
            "backend.data_lake.ingest_daily_events._fetch_news_for_date_range",
            return_value=[],
        ), patch(
            "backend.data_lake.ingest_daily_events._fetch_sec_8k_events",
            return_value=[in_window, out_window],
        ):
            events = collect_incremental_events(
                date(2026, 5, 29),
                date(2026, 5, 29),
                ["AAPL"],
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "a")


if __name__ == "__main__":
    unittest.main()
