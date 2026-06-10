"""Unit tests for chunked fetch helpers (offline, no network)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from backend.connectors.fetch_utils import paginate_cursor, paginate_offset
from backend.connectors.youtube import channel_uploads_playlist_id
from backend.connectors.yfinance_batch import (
    chunk_tickers,
    close_series_by_ticker,
    daily_change_pct_from_close,
)


class TestFetchUtils(unittest.TestCase):
    def test_paginate_cursor_stops_on_empty_cursor(self):
        pages = iter([
            (["a", "b"], "c1"),
            (["c"], None),
        ])

        def _fetch(cursor):
            return next(pages)

        out = paginate_cursor(_fetch, max_pages=5, inter_page_delay=0)
        self.assertEqual(out, ["a", "b", "c"])

    def test_paginate_offset_stops_on_short_page(self):
        def _fetch(offset):
            if offset == 0:
                return ["x", "y"]
            return []

        out = paginate_offset(_fetch, page_size=10, max_pages=3, inter_page_delay=0)
        self.assertEqual(out, ["x", "y"])


class TestYouTubePlaylistId(unittest.TestCase):
    def test_uc_to_uu_conversion(self):
        self.assertEqual(
            channel_uploads_playlist_id("UCvM5YYWwfLwTyaKZPMCIRwg"),
            "UUvM5YYWwfLwTyaKZPMCIRwg",
        )


class TestYfinanceBatch(unittest.TestCase):
    def test_chunk_tickers_dedupes(self):
        chunks = chunk_tickers(["AAPL", "aapl", "MSFT"], chunk_size=1)
        self.assertEqual(chunks, [["AAPL"], ["MSFT"]])

    def test_daily_change_pct_from_close(self):
        close = pd.Series([100.0, 105.0])
        self.assertEqual(daily_change_pct_from_close(close), 5.0)

    def test_close_series_single_ticker(self):
        raw = pd.DataFrame({"Close": [1.0, 2.0], "Open": [1.0, 2.0]})
        out = close_series_by_ticker(raw, ["AAPL"])
        self.assertIn("AAPL", out)
        self.assertEqual(len(out["AAPL"]), 2)

    @patch("backend.connectors.yfinance_batch._download_chunk")
    def test_batch_daily_change_pct(self, mock_dl):
        mock_dl.return_value = pd.DataFrame({"Close": [100.0, 110.0]})
        from backend.connectors.yfinance_batch import batch_daily_change_pct

        out = batch_daily_change_pct(["AAPL"])
        self.assertEqual(out["AAPL"], 10.0)


if __name__ == "__main__":
    unittest.main()
