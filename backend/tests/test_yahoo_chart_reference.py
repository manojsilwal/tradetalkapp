"""Offline tests for Yahoo chart JSON parsing (no network)."""
import json
import unittest
from unittest.mock import patch

from backend.connectors.yahoo_chart_reference import fetch_yahoo_chart_quote


class TestYahooChartReference(unittest.TestCase):
    def test_parses_regular_meta(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 123.45,
                            "chartPreviousClose": 120.0,
                            "marketState": "REGULAR",
                            "regularMarketTime": 1700000000,
                        }
                    }
                ]
            }
        }
        raw = json.dumps(payload).encode()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return raw

        with patch("urllib.request.urlopen", return_value=_Resp()):
            q = fetch_yahoo_chart_quote("TEST")
        self.assertEqual(q.symbol, "TEST")
        self.assertEqual(q.regular_market_price, 123.45)
        self.assertEqual(q.chart_previous_close, 120.0)
        self.assertAlmostEqual(q.change_pct, (123.45 - 120.0) / 120.0 * 100.0, places=4)
        self.assertEqual(q.market_state, "REGULAR")
        self.assertEqual(q.regular_market_time, 1700000000)


if __name__ == "__main__":
    unittest.main()
