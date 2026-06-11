"""Unit tests for stock fundamentals connector and API endpoint."""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
from datetime import datetime

from backend.connectors.stock_fundamentals import fetch_stock_fundamentals
from backend.routers.analysis import get_stock_fundamentals

# Helper to mock yfinance Ticker history
class MockHistoryDataFrame(pd.DataFrame):
    pass

class TestStockFundamentals(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Sample data for Ticker.info
        self.mock_info = {
            "marketCap": 2000000000000,
            "trailingPE": 25.5,
            "priceToSalesTrailing12Months": 7.2,
            "enterpriseToEbitda": 18.3,
            "freeCashflow": 80000000000,
            "sharesOutstanding": 15000000000,
            "profitMargins": 0.25,
            "operatingMargins": 0.30,
            "earningsGrowth": 0.08,
            "revenueGrowth": 0.05,
            "totalCash": 60000000000,
            "totalDebt": 100000000000,
            "dividendYield": 0.0055,
            "payoutRatio": 0.15,
            "longName": "Apple Inc.",
            "exchange": "NMS",
            "currentPrice": 172.50,
            "previousClose": 174.00,
        }

        # Sample price history
        dates = pd.date_range(end=datetime.today(), periods=5, freq="D")
        self.mock_history_df = pd.DataFrame(
            {
                "Open": [170.0, 171.0, 172.0, 173.0, 174.0],
                "High": [172.0, 173.0, 174.0, 175.0, 176.0],
                "Low": [169.0, 170.0, 171.0, 172.0, 173.0],
                "Close": [171.0, 172.0, 173.0, 174.0, 175.0],
                "Volume": [1000000, 1100000, 1200000, 1300000, 1400000],
            },
            index=dates,
        )

        # Sample financial statement DataFrames
        fin_dates = [datetime(2023, 12, 31), datetime(2024, 12, 31)]
        self.mock_annual_stmt = pd.DataFrame(
            [
                [385000000000, 395000000000],  # Total Revenue row
                [96000000000, 100000000000],   # Net Income row
            ],
            index=["Total Revenue", "Net Income"],
            columns=fin_dates,
        )

        self.mock_quarterly_stmt = pd.DataFrame(
            [
                [90000000000, 95000000000],  # Total Revenue row
                [22000000000, 24000000000],  # Net Income row
            ],
            index=["Total Revenue", "Net Income"],
            columns=fin_dates,
        )

    @patch("yfinance.Ticker")
    def test_connector_fetches_and_structures_data(self, mock_ticker_class):
        # Set up mock ticker instance
        mock_ticker_instance = MagicMock()
        mock_ticker_instance.info = self.mock_info
        mock_ticker_instance.history.return_value = self.mock_history_df
        mock_ticker_instance.income_stmt = self.mock_annual_stmt
        mock_ticker_instance.quarterly_income_stmt = self.mock_quarterly_stmt
        mock_ticker_class.return_value = mock_ticker_instance

        # Call the connector
        result = fetch_stock_fundamentals("AAPL")

        # Basic verification
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["company_info"]["company_name"], "Apple Inc.")
        self.assertEqual(result["company_info"]["exchange"], "NASDAQ")
        self.assertEqual(result["company_info"]["current_price"], 172.50)
        self.assertEqual(result["company_info"]["price_change"], -1.5)
        self.assertEqual(result["company_info"]["price_change_pct"], -0.8621)

        # Check metrics
        self.assertEqual(result["metrics"]["valuation"]["market_cap"], 2000000000000)
        self.assertEqual(result["metrics"]["valuation"]["trailing_pe"], 25.5)
        self.assertEqual(result["metrics"]["cash_flow"]["fcf_per_share"], 5.33)
        self.assertEqual(result["metrics"]["cash_flow"]["fcf_yield"], 0.04)

        # Check financials
        self.assertEqual(len(result["financials"]["annual"]), 2)
        self.assertEqual(result["financials"]["annual"][0]["revenue"], 385000000000)
        self.assertEqual(result["financials"]["annual"][1]["net_income"], 100000000000)

        # Check price history
        self.assertIn("1d", result["price_history"])
        self.assertEqual(len(result["price_history"]["1d"]), 5)
        self.assertEqual(result["price_history"]["1d"][0]["close"], 171.0)

    @patch("backend.routers.analysis.fetch_stock_fundamentals")
    async def test_endpoint_returns_json(self, mock_fetch):
        mock_fetch.return_value = {"ticker": "MSFT", "company_info": {"company_name": "Microsoft"}}
        result = await get_stock_fundamentals("MSFT")
        self.assertEqual(result["ticker"], "MSFT")
        self.assertEqual(result["company_info"]["company_name"], "Microsoft")

if __name__ == "__main__":
    unittest.main()
