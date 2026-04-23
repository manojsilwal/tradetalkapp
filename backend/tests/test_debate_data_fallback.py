"""Debate data connector — empty history + fallback paths (mocked yfinance)."""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd


class TestDebateDataFallback(unittest.TestCase):
    @patch("backend.connectors.debate_data.time.sleep")
    @patch("backend.connectors.quote_fallbacks.fetch_us_equity_spot")
    @patch("yfinance.Ticker")
    def test_empty_history_uses_fallback_spot(self, mock_ticker_cls, mock_fb, _sleep):
        mock_fb.return_value = (222.0, "stooq")

        hist_empty = MagicMock()
        hist_empty.empty = True

        inst = MagicMock()
        inst.history.return_value = hist_empty
        inst.info = {"longName": "Apple Inc"}
        inst.fast_info = {}
        mock_ticker_cls.return_value = inst

        from backend.connectors.debate_data import fetch_debate_data

        data = asyncio.run(fetch_debate_data("AAPL"))
        self.assertEqual(data["current_price"], 222.0)
        self.assertEqual(data["spot_price_source"], "stooq")
        self.assertTrue(data["market_data_degraded"])

    @patch("backend.connectors.debate_data.time.sleep")
    @patch("backend.connectors.quote_fallbacks.fetch_us_equity_spot")
    @patch("yfinance.Ticker")
    def test_history_used_when_present(self, mock_ticker_cls, mock_fb, _sleep):
        mock_fb.return_value = None

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
        mock_fb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
