"""Tests for momentum data connector (yfinance mocked)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from backend.connectors.momentum_data import (
    clear_benchmark_cache_for_tests,
    fetch_momentum_inputs_sync,
    sector_etf_for,
)
from backend.data_errors import InsufficientDataError


def _fake_hist(n: int = 200) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp("2024-06-01"), periods=n)
    close = [100.0 + i * 0.1 for i in range(len(dates))]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Volume": [1_000_000] * len(dates),
        },
        index=dates,
    )


class TestMomentumData(unittest.TestCase):
    def setUp(self) -> None:
        clear_benchmark_cache_for_tests()

    def tearDown(self) -> None:
        clear_benchmark_cache_for_tests()

    def test_sector_etf_mapping(self) -> None:
        self.assertEqual(sector_etf_for("Technology"), "XLK")
        self.assertEqual(sector_etf_for("Unknown Sector"), "SPY")

    @patch("backend.connectors.momentum_data._fetch_history_sync")
    @patch("yfinance.Ticker")
    def test_fetch_success(self, mock_ticker_cls: MagicMock, mock_hist: MagicMock) -> None:
        mock_hist.side_effect = lambda sym, period="1y": _fake_hist(200)
        mock_ticker_cls.return_value.info = {
            "sector": "Technology",
            "industry": "Software",
            "marketCap": 1e11,
            "beta": 1.1,
        }
        stock_df, spy_df, sector_df, meta = fetch_momentum_inputs_sync("AAPL", {})
        self.assertFalse(stock_df.empty)
        self.assertFalse(spy_df.empty)
        self.assertEqual(meta["ticker"], "AAPL")
        self.assertEqual(meta["sector_etf"], "XLK")

    @patch("backend.connectors.momentum_data._fetch_history_sync")
    @patch("yfinance.Ticker")
    def test_fetch_empty_stock_raises(self, mock_ticker_cls: MagicMock, mock_hist: MagicMock) -> None:
        mock_hist.side_effect = lambda sym, period="1y": (
            pd.DataFrame() if sym == "BAD" else _fake_hist(200)
        )
        mock_ticker_cls.return_value.info = {}
        with self.assertRaises(InsufficientDataError):
            fetch_momentum_inputs_sync("BAD", {})


if __name__ == "__main__":
    unittest.main()
