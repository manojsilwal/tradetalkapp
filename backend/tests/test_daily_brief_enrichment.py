"""Daily brief ticker metadata enrichment (offline)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.daily_brief import (
    _enrichment_is_usable,
    _fetch_ticker_enrichment,
    enrich_daily_brief_rows,
)


class TestDailyBriefEnrichment(unittest.TestCase):
    def test_enrichment_is_usable_requires_real_fields(self):
        self.assertFalse(_enrichment_is_usable(None))
        self.assertFalse(_enrichment_is_usable({"industry": "Unknown"}))
        self.assertTrue(_enrichment_is_usable({"market_cap": 1e9}))
        self.assertTrue(_enrichment_is_usable({"industry": "Semiconductors"}))

    @patch("yfinance.Ticker")
    def test_fast_info_market_cap_when_info_empty(self, mock_ticker_cls):
        ticker = MagicMock()
        ticker.fast_info = {"marketCap": 42_000_000_000}
        ticker.info = {}
        mock_ticker_cls.return_value = ticker

        meta = _fetch_ticker_enrichment("CPRT")

        self.assertIsNotNone(meta)
        self.assertEqual(meta["market_cap"], 42_000_000_000)
        self.assertEqual(meta["industry"], "Diversified Support Services")

    @patch("backend.actionable_companies.fetch_fundamentals")
    @patch("yfinance.Ticker")
    def test_fundamentals_fallback_when_yfinance_empty(self, mock_ticker_cls, mock_fund):
        ticker = MagicMock()
        ticker.fast_info = {}
        ticker.info = {}
        mock_ticker_cls.return_value = ticker
        mock_fund.return_value = {
            "company_name": "Copart, Inc.",
            "sector": "Industrials",
            "industry": "Specialty Business Services",
            "market_cap": 50_000_000_000,
            "trailing_pe": 28.5,
            "forward_pe": 26.0,
        }

        meta = _fetch_ticker_enrichment("CPRT")

        self.assertEqual(meta["industry"], "Specialty Business Services")
        self.assertEqual(meta["market_cap"], 50_000_000_000)
        self.assertEqual(meta["pe_ratio"], 28.5)

    @patch("backend.actionable_companies.fetch_fundamentals")
    @patch("yfinance.Ticker")
    def test_gics_reference_when_yfinance_rate_limited(self, mock_ticker_cls, mock_fund):
        ticker = MagicMock()
        ticker.fast_info = {}
        ticker.info = {}
        mock_ticker_cls.return_value = ticker
        mock_fund.side_effect = RuntimeError("rate limited")

        meta = _fetch_ticker_enrichment("CPRT")

        self.assertIsNotNone(meta)
        self.assertEqual(meta["industry"], "Diversified Support Services")
        self.assertEqual(meta["sector"], "Industrials")
        self.assertIsNone(meta["market_cap"])

    @patch("backend.actionable_companies.fetch_fundamentals")
    @patch("yfinance.Ticker")
    def test_mrvl_manual_gics_override(self, mock_ticker_cls, mock_fund):
        ticker = MagicMock()
        ticker.fast_info = {}
        ticker.info = {}
        mock_ticker_cls.return_value = ticker
        mock_fund.side_effect = RuntimeError("rate limited")

        meta = _fetch_ticker_enrichment("MRVL")

        self.assertIsNotNone(meta)
        self.assertEqual(meta["industry"], "Semiconductors")

    @patch("backend.daily_brief._fetch_ticker_enrichment")
    def test_enrich_populates_rows(self, mock_fetch):
        mock_fetch.return_value = {
            "industry": "Semiconductors",
            "market_cap": 80_000_000_000,
            "pe_ratio": 45.0,
            "forward_pe": 40.0,
            "insider_sentiment": "1.2% Insiders",
        }
        rows = [{"symbol": "MRVL", "bucket": "loser", "rank": 1}]
        enrich_daily_brief_rows(rows)
        self.assertEqual(rows[0]["industry"], "Semiconductors")
        self.assertEqual(rows[0]["market_cap"], 80_000_000_000)
        self.assertEqual(rows[0]["pe_ratio"], 45.0)


if __name__ == "__main__":
    unittest.main()
