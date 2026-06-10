"""Debate data connector — truthful-data contract (mocked yfinance).

When 6-month history is unavailable, the connector must raise
InsufficientDataError instead of fabricating spot-only or zeroed records.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from backend.data_errors import InsufficientDataError


class TestDebateDataTruthfulness(unittest.TestCase):
    @patch("backend.connectors.debate_data.time.sleep")
    @patch("yfinance.Ticker")
    def test_empty_history_raises_insufficient_data(self, mock_ticker_cls, _sleep):
        hist_empty = MagicMock()
        hist_empty.empty = True

        inst = MagicMock()
        inst.history.return_value = hist_empty
        inst.info = {"longName": "Apple Inc"}
        inst.fast_info = {}
        mock_ticker_cls.return_value = inst

        from backend.connectors.debate_data import fetch_debate_data

        with self.assertRaises(InsufficientDataError) as ctx:
            asyncio.run(fetch_debate_data("AAPL"))
        self.assertEqual(ctx.exception.ticker, "AAPL")
        self.assertIn("price_history_6mo", ctx.exception.missing)
        payload = ctx.exception.to_payload()
        self.assertEqual(payload["error"], "insufficient_data")
        self.assertEqual(payload["source"], "yfinance")

    @patch("backend.connectors.debate_data.time.sleep")
    @patch("yfinance.Ticker")
    def test_history_used_when_present(self, mock_ticker_cls, _sleep):
        hist = pd.DataFrame({"Close": [100.0, 110.0, 120.0]})
        hist.attrs = {}

        inst = MagicMock()
        inst.history.return_value = hist
        inst.info = {
            "longName": "Apple Inc",
            "fiftyTwoWeekHigh": 130.0,
            "fiftyTwoWeekLow": 90.0,
            "beta": 1.1,
        }
        inst.fast_info = {}
        mock_ticker_cls.return_value = inst

        from backend.connectors.debate_data import fetch_debate_data

        data = asyncio.run(fetch_debate_data("AAPL"))
        self.assertEqual(data["current_price"], 120.0)
        self.assertEqual(data["spot_price_source"], "yfinance_history")
        self.assertFalse(data["market_data_degraded"])

    @patch("backend.connectors.debate_data.time.sleep")
    @patch("yfinance.Ticker")
    def test_yfinance_exception_raises_insufficient_data(self, mock_ticker_cls, _sleep):
        """When yfinance raises (blocked IP, outage), no fabricated record is returned."""
        mock_ticker_cls.side_effect = RuntimeError("Yahoo rate limit / empty session")

        from backend.connectors.debate_data import fetch_debate_data

        with self.assertRaises(InsufficientDataError) as ctx:
            asyncio.run(fetch_debate_data("MSFT"))
        self.assertEqual(ctx.exception.ticker, "MSFT")


if __name__ == "__main__":
    unittest.main()
