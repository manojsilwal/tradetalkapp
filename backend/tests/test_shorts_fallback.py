"""
Tests for ShortsConnector fallback mechanism.
Verifies that when yfinance is blocked or missing short interest statistics,
we fall back to scraping stockanalysis.com.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from backend.data_errors import InsufficientDataError
from backend.connectors.shorts import ShortsConnector
import backend.connector_cache as cc

# Mock HTML data for stockanalysis.com statistics page
MOCK_HTML_OK = """
<html>
<body>
    <table>
        <tr>
            <td>Short % of Float</td>
            <td>12.34%</td>
        </tr>
        <tr>
            <td>Short Ratio (days to cover)</td>
            <td>4.56</td>
        </tr>
    </table>
</body>
</html>
"""

MOCK_HTML_EMPTY = """
<html>
<body>
    <p>No tables here.</p>
</body>
</html>
"""

class TestShortsConnectorFallback(unittest.TestCase):
    def setUp(self) -> None:
        cc._store.clear()
        self.conn = ShortsConnector()

    @patch("yfinance.Ticker")
    def test_yfinance_success_does_not_scrape(self, mock_ticker) -> None:
        # yfinance succeeds, returns short stats
        inst = MagicMock()
        inst.info = {"shortPercentOfFloat": 0.15, "shortRatio": 3.0}
        mock_ticker.return_value = inst

        with patch("urllib.request.urlopen") as mock_urlopen:
            result = asyncio.run(self.conn.fetch_data(ticker="AAPL"))

            # Should return yfinance data
            self.assertEqual(result["short_interest_ratio"], 15.0)
            self.assertEqual(result["days_to_cover"], 3.0)
            self.assertEqual(result["source"], "yfinance API (Live)")

            # Should NOT have called urlopen to scrape stockanalysis
            mock_urlopen.assert_not_called()

    @patch("yfinance.Ticker")
    @patch("urllib.request.urlopen")
    def test_yfinance_failure_stockanalysis_success(self, mock_urlopen, mock_ticker) -> None:
        # yfinance returns None / empty dict
        inst = MagicMock()
        inst.info = {}
        mock_ticker.return_value = inst

        # urllib.request.urlopen returns mock HTML statistics page
        mock_resp = MagicMock()
        mock_resp.read.return_value = MOCK_HTML_OK.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = asyncio.run(self.conn.fetch_data(ticker="AAPL"))

        # Should return StockAnalysis data
        self.assertEqual(result["short_interest_ratio"], 12.34)
        self.assertEqual(result["days_to_cover"], 4.56)
        self.assertEqual(result["source"], "StockAnalysis Fallback Scraper")

    @patch("yfinance.Ticker")
    @patch("urllib.request.urlopen")
    def test_yfinance_exception_stockanalysis_success(self, mock_urlopen, mock_ticker) -> None:
        # yfinance raises an exception (e.g. rate-limit or connection block)
        mock_ticker.side_effect = RuntimeError("Rate limited or blocked")

        # urllib.request.urlopen returns mock HTML statistics page
        mock_resp = MagicMock()
        mock_resp.read.return_value = MOCK_HTML_OK.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = asyncio.run(self.conn.fetch_data(ticker="AAPL"))

        # Should still succeed and return StockAnalysis data
        self.assertEqual(result["short_interest_ratio"], 12.34)
        self.assertEqual(result["days_to_cover"], 4.56)
        self.assertEqual(result["source"], "StockAnalysis Fallback Scraper")

    @patch("yfinance.Ticker")
    @patch("urllib.request.urlopen")
    def test_both_fail_raises_insufficient_data(self, mock_urlopen, mock_ticker) -> None:
        # yfinance fails
        inst = MagicMock()
        inst.info = {}
        mock_ticker.return_value = inst

        # urllib.request.urlopen returns empty HTML
        mock_resp = MagicMock()
        mock_resp.read.return_value = MOCK_HTML_EMPTY.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with self.assertRaises(InsufficientDataError) as ctx:
            asyncio.run(self.conn.fetch_data(ticker="AAPL"))

        self.assertIn("AAPL", ctx.exception.message)
        self.assertIn("short_interest_ratio", ctx.exception.missing)

if __name__ == "__main__":
    unittest.main()
