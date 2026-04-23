"""
Optional live checks: Stooq + FinCrawler quote chain (real network).

Run when debugging production-style failures (yfinance empty from cloud):

  FINCRAWLER_INTEGRATION_TEST=1 FINCRAWLER_URL=https://fincrawler.onrender.com \\
    FINCRAWLER_KEY=<same as FinCrawler API_KEY> PYTHONPATH=. \\
    python -m unittest backend.tests.test_fincrawler_integration_live -v

Skips automatically when FINCRAWLER_INTEGRATION_TEST is unset.
"""
import os
import unittest


@unittest.skipUnless(
    os.environ.get("FINCRAWLER_INTEGRATION_TEST", "").strip().lower() in ("1", "true", "yes"),
    "Set FINCRAWLER_INTEGRATION_TEST=1 plus FINCRAWLER_URL and FINCRAWLER_KEY for live FinCrawler",
)
class TestFinCrawlerLiveIntegration(unittest.TestCase):
    def test_fetch_us_equity_spot_returns_stooq_or_fincrawler(self):
        from backend.connectors.quote_fallbacks import fetch_us_equity_spot

        if not os.environ.get("FINCRAWLER_URL") or not os.environ.get("FINCRAWLER_KEY"):
            self.skipTest("FINCRAWLER_URL and FINCRAWLER_KEY required for FinCrawler hop")

        r = fetch_us_equity_spot("AAPL")
        self.assertIsNotNone(r, "Expected Stooq or FinCrawler to return a US spot for AAPL")
        price, label = r
        self.assertGreater(price, 0)
        self.assertIn(label, ("stooq", "fincrawler"))


if __name__ == "__main__":
    unittest.main()
