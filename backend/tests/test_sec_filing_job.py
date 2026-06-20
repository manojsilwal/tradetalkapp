import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import time
from datetime import datetime, timezone

from backend.sec_filing_job import run_sec_filing_job, fetch_recent_filing_tickers
from backend.connectors.scorecard_data import ScorecardData


class TestSecFilingJob(unittest.IsolatedAsyncioTestCase):
    @patch("backend.sec_filing_job.get_all_unique_portfolio_tickers")
    @patch("backend.sec_filing_job.get_stock_sec_info")
    @patch("backend.sec_filing_job.fetch_recent_filing_tickers")
    @patch("backend.sec_filing_job.fetch_scorecard_data")
    @patch("backend.sec_filing_job.fc")
    @patch("backend.sec_filing_job.llm_client")
    @patch("backend.sec_filing_job.upsert_stock_sec_info")
    async def test_run_sec_filing_job_filtering(
        self,
        mock_upsert,
        mock_llm,
        mock_fc,
        mock_fetch_scorecard,
        mock_recent_filings,
        mock_get_cache,
        mock_get_tickers
    ):
        # 1. Setup portfolio tickers: AAPL (uncached), MSFT (cached, recent filing), TSLA (cached, no recent filing)
        mock_get_tickers.return_value = ["AAPL", "MSFT", "TSLA"]
        
        # 2. Mock database cache
        mock_get_cache.side_effect = lambda ticker: {
            "AAPL": None,  # Not in cache
            "MSFT": {"ticker": "MSFT", "ceo_name": "Satya Nadella", "sitg_score": 4.0, "updated_at": time.time() - 3600}, # Cached
            "TSLA": {"ticker": "TSLA", "ceo_name": "Elon Musk", "sitg_score": 8.0, "updated_at": time.time() - 3600}     # Cached
        }[ticker]

        # 3. Mock Atom feed return: Only MSFT has recent filing
        mock_recent_filings.return_value = {"MSFT"}

        # 4. Mock fundamentals data for processed tickers (AAPL, MSFT)
        data_aapl = ScorecardData(
            ticker="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            industry="Consumer Electronics",
            ceo_name="Tim Cook",
            insider_buy_count_12m=0,
            insider_sell_count_12m=4,
            insider_net_shares_12m=-10000,
            held_percent_insiders=0.07,
            eps_growth_pct=8.5,
            revenue_growth_pct=5.0,
            pt_upside_pct=12.0,
            dividend_yield_pct=0.5,
            forward_pe=28.0,
            historical_avg_pe=25.0,
            beta=1.2,
            debt_to_equity=1.5,
            current_price=180.0,
            fields_missing=[]
        )
        data_msft = ScorecardData(
            ticker="MSFT",
            company_name="Microsoft Corp.",
            sector="Technology",
            industry="Software",
            ceo_name="Satya Nadella",
            insider_buy_count_12m=1,
            insider_sell_count_12m=1,
            insider_net_shares_12m=0,
            held_percent_insiders=0.08,
            eps_growth_pct=10.0,
            revenue_growth_pct=8.0,
            pt_upside_pct=15.0,
            dividend_yield_pct=0.8,
            forward_pe=32.0,
            historical_avg_pe=30.0,
            beta=1.1,
            debt_to_equity=0.8,
            current_price=420.0,
            fields_missing=[]
        )
        
        mock_fetch_scorecard.side_effect = lambda ticker: {
            "AAPL": data_aapl,
            "MSFT": data_msft
        }[ticker]

        mock_fc.enabled = True
        mock_fc.get_sec_filing = AsyncMock(return_value="MockedDEF14AText")

        # 5. Mock LLM scoring
        mock_llm.generate_sitg_score = AsyncMock()
        mock_llm.generate_sitg_score.side_effect = lambda ticker, ctx: {
            "AAPL": {
                "ceo_name": "Tim Cook",
                "sitg_score": 9.5,
                "ceo_base_salary": 3000000,
                "sitg_value": 450000000,  # multiple = 150 -> Founder-Level
                "sitg_percentile_tier": "Founder-Level SITG"
            },
            "MSFT": {
                "ceo_name": "Satya Nadella",
                "sitg_score": 4.0,
                "ceo_base_salary": 2500000,
                "sitg_value": 5000000,  # multiple = 2 -> Below Average
                "sitg_percentile_tier": "Below Average SITG"
            }
        }[ticker]

        # 6. Execute job
        result = await run_sec_filing_job()

        # 7. Assertions
        self.assertTrue(result["ok"])
        self.assertEqual(result["processed"], 2)  # AAPL & MSFT
        self.assertEqual(result["skipped"], 1)    # TSLA skipped
        self.assertEqual(result["failed"], 0)

        # Verify database upserts only called for AAPL and MSFT
        self.assertEqual(mock_upsert.call_count, 2)
        mock_upsert.assert_any_call(
            ticker="AAPL",
            ceo_name="Tim Cook",
            sitg_score=9.5,
            ceo_base_salary=3000000,
            sitg_value=450000000,
            sitg_multiple=150.0,
            sitg_percentile_tier="Founder-Level SITG",
            insider_buy_count_12m=0,
            insider_sell_count_12m=4,
            insider_net_shares_12m=-10000,
            held_percent_insiders=0.07
        )
        mock_upsert.assert_any_call(
            ticker="MSFT",
            ceo_name="Satya Nadella",
            sitg_score=4.0,
            ceo_base_salary=2500000,
            sitg_value=5000000,
            sitg_multiple=2.0,
            sitg_percentile_tier="Below Average SITG",
            insider_buy_count_12m=1,
            insider_sell_count_12m=1,
            insider_net_shares_12m=0,
            held_percent_insiders=0.08
        )

    @patch("urllib.request.urlopen")
    @patch("backend.sec_filing_job._load_cik_map")
    @patch("backend.sec_filing_job._CIK_MAP")
    async def test_fetch_recent_filing_tickers(self, mock_cik_map, mock_load_map, mock_urlopen):
        # Mock CIK to ticker translation
        mock_cik_map.items.return_value = [
            ("ACN", "0001467373"),
            ("MSFT", "0000789019")
        ]
        
        # Mock Atom feed XML response
        mock_xml = b"""<?xml version="1.0" encoding="ISO-8859-1" ?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <title>10-Q - Accenture plc (0001467373) (Filer)</title>
                <updated>""" + datetime.now(timezone.utc).isoformat().encode('utf-8') + b"""</updated>
            </entry>
            <entry>
                <title>10-Q - Microsoft Corp (0000789019) (Filer)</title>
                <updated>2020-01-01T00:00:00Z</updated>
            </entry>
        </feed>"""
        
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_xml
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        recent = await fetch_recent_filing_tickers(days=1)
        
        # ACN should be returned (updated today)
        self.assertIn("ACN", recent)
        # MSFT should not (updated in 2020)
        self.assertNotIn("MSFT", recent)
