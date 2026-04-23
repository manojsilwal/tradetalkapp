"""Unit tests for US spot fallbacks (no live FinCrawler)."""
import unittest
from unittest.mock import MagicMock, patch

from backend.connectors import quote_fallbacks


class TestStooq(unittest.TestCase):
    @patch.object(quote_fallbacks.urllib.request, "urlopen")
    def test_stooq_parses_close(self, mock_open):
        csv_doc = """Symbol,Date,Time,Open,High,Low,Close,Volume\r\naapl.us,2024-01-02,17:00:01,100,101,99,123.45,999\r\n"""

        class _CM:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return csv_doc.encode()

        mock_open.return_value = _CM()

        spot = quote_fallbacks._stooq_us_spot("AAPL")
        self.assertEqual(spot, 123.45)


class TestFetchChain(unittest.TestCase):
    @patch.object(quote_fallbacks, "_fincrawler_quote_sync", return_value=None)
    @patch.object(quote_fallbacks, "_stooq_us_spot", return_value=None)
    def test_prefers_stooq(self, mock_stooq, mock_fc):
        mock_stooq.return_value = 200.0
        r = quote_fallbacks.fetch_us_equity_spot("MSFT")
        self.assertEqual(r, (200.0, "stooq"))
        mock_fc.assert_not_called()

    @patch.object(quote_fallbacks, "_fincrawler_quote_sync", return_value=199.5)
    @patch.object(quote_fallbacks, "_stooq_us_spot", return_value=None)
    def test_falls_through_to_fincrawler(self, mock_stooq, mock_fc):
        r = quote_fallbacks.fetch_us_equity_spot("MSFT")
        self.assertEqual(r, (199.5, "fincrawler"))

    def test_invalid_ticker_returns_none(self):
        self.assertIsNone(quote_fallbacks.fetch_us_equity_spot("FOO1"))

    @patch.object(quote_fallbacks.urllib.request, "urlopen")
    def test_stooq_class_b_share(self, mock_open):
        csv_doc = """Symbol,Date,Time,Open,High,Low,Close,Volume\r\nbrk-b.us,2026-01-02,17:00:01,400,410,399,420.5,1\r\n"""

        class _CM:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return csv_doc.encode()

        mock_open.return_value = _CM()
        spot = quote_fallbacks._stooq_us_spot("BRK.B")
        self.assertEqual(spot, 420.5)


if __name__ == "__main__":
    unittest.main()
