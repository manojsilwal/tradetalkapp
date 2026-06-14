"""Unit tests for US spot fallbacks (no live FinCrawler / Stooq)."""
import os
import unittest
from unittest.mock import patch

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

    @patch.object(quote_fallbacks.urllib.request, "urlopen")
    def test_stooq_bot_wall_returns_none(self, mock_open):
        html = b"<!DOCTYPE html><html><script>__verify</script></html>"

        class _CM:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return html

        mock_open.return_value = _CM()
        self.assertIsNone(quote_fallbacks._stooq_us_spot("AAPL"))

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


class TestFetchChain(unittest.TestCase):
    @patch.object(quote_fallbacks, "_stooq_us_spot", return_value=None)
    @patch.object(quote_fallbacks, "_yahoo_chart_spot", return_value=None)
    @patch.object(quote_fallbacks, "_fincrawler_quote_sync", return_value=199.5)
    def test_fincrawler_last_resort(self, _mock_fc, _mock_yahoo, _mock_stooq):
        r = quote_fallbacks.fetch_us_equity_spot("MSFT")
        self.assertEqual(r, (199.5, "fincrawler"))

    @patch.object(quote_fallbacks, "_stooq_us_spot", return_value=200.0)
    @patch.object(quote_fallbacks, "_yahoo_chart_spot", return_value=201.0)
    @patch.object(quote_fallbacks, "_fincrawler_quote_sync", return_value=199.5)
    def test_prefers_stooq_over_fincrawler(self, _mock_fc, _mock_yahoo, _mock_stooq):
        r = quote_fallbacks.fetch_us_equity_spot("MSFT")
        self.assertEqual(r, (200.0, "stooq"))
        _mock_fc.assert_not_called()

    @patch.object(quote_fallbacks, "_stooq_us_spot", return_value=200.0)
    @patch.object(quote_fallbacks, "_yahoo_chart_spot", return_value=201.0)
    @patch.object(quote_fallbacks, "_fincrawler_quote_sync", return_value=None)
    def test_prefers_stooq_before_yahoo_chart_by_default(self, _mock_fc, _mock_yahoo, _mock_stooq):
        r = quote_fallbacks.fetch_us_equity_spot("MSFT")
        self.assertEqual(r, (200.0, "stooq"))
        _mock_yahoo.assert_not_called()

    @patch.dict(os.environ, {"QUOTE_FALLBACK_ALLOW_YAHOO_CHART": "1"}, clear=False)
    @patch.object(quote_fallbacks, "_stooq_us_spot", return_value=None)
    @patch.object(quote_fallbacks, "_yahoo_chart_spot", return_value=201.0)
    @patch.object(quote_fallbacks, "_fincrawler_quote_sync", return_value=None)
    def test_yahoo_chart_when_explicitly_enabled(self, _mock_fc, _mock_yahoo, _mock_stooq):
        r = quote_fallbacks.fetch_us_equity_spot("MSFT")
        self.assertEqual(r, (201.0, "yahoo_chart"))

    @patch.object(quote_fallbacks, "_stooq_us_spot", return_value=200.0)
    @patch.object(quote_fallbacks, "_yahoo_chart_spot", return_value=None)
    @patch.object(quote_fallbacks, "_fincrawler_quote_sync", return_value=None)
    def test_falls_through_to_stooq(self, _mock_fc, _mock_yahoo, _mock_stooq):
        r = quote_fallbacks.fetch_us_equity_spot("MSFT")
        self.assertEqual(r, (200.0, "stooq"))

    def test_invalid_ticker_returns_none(self):
        self.assertIsNone(quote_fallbacks.fetch_us_equity_spot("FOO1"))

    def test_is_html_bot_wall(self):
        self.assertTrue(quote_fallbacks._is_html_bot_wall("<!DOCTYPE html><script>"))
        self.assertFalse(quote_fallbacks._is_html_bot_wall("Symbol,Date,Close\naapl.us,2024,100"))


if __name__ == "__main__":
    unittest.main()
