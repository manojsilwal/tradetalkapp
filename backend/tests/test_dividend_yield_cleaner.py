import unittest
from unittest.mock import patch
from backend.connectors.base import clean_dividend_yield

class TestDividendYieldCleaner(unittest.TestCase):
    def test_clean_dividend_yield_none(self):
        self.assertEqual(clean_dividend_yield(None), 0.0)

    def test_clean_dividend_yield_invalid(self):
        self.assertEqual(clean_dividend_yield("not a float"), 0.0)

    def test_clean_dividend_yield_zero_or_negative(self):
        self.assertEqual(clean_dividend_yield(0.0), 0.0)
        self.assertEqual(clean_dividend_yield(-1.2), 0.0)

    def test_clean_dividend_yield_high_value(self):
        # Values > 0.25 are parsed as percentage directly
        self.assertEqual(clean_dividend_yield(3.21), 3.21)
        self.assertEqual(clean_dividend_yield(0.82), 0.82)

    def test_clean_dividend_yield_old_yfinance_ratio(self):
        # Mock yfinance version to represent older version that returns ratios
        with patch("yfinance.__version__", "0.2.18"):
            self.assertAlmostEqual(clean_dividend_yield(0.0321), 3.21, places=4)
            self.assertAlmostEqual(clean_dividend_yield(0.0082), 0.82, places=4)

    def test_clean_dividend_yield_new_yfinance_percent(self):
        # Mock yfinance version to represent newer version that returns percentages
        with patch("yfinance.__version__", "1.3.0"):
            self.assertEqual(clean_dividend_yield(0.0321), 0.0321)
            self.assertEqual(clean_dividend_yield(0.0082), 0.0082)
