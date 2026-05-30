"""Unit tests for multi-image holdings JSON parsing (no Gemini)."""
import unittest

from backend.portfolio_holdings_reconcile import holdings_dicts_from_model_json


class TestPortfolioParseImages(unittest.TestCase):
    def test_parses_holdings_array(self):
        text = '{"holdings":[{"ticker":"AAPL","shares":10,"avg_cost":150.5},{"ticker":"MSFT","shares":2}]}'
        rows = holdings_dicts_from_model_json(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["shares"], 10)

    def test_skips_invalid_rows(self):
        text = '{"holdings":[{"ticker":"GOOG","shares":1},{"bad":true},{"ticker":"","shares":1}]}'
        rows = holdings_dicts_from_model_json(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "GOOG")

    def test_empty_holdings(self):
        rows = holdings_dicts_from_model_json('{"holdings":[]}')
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
