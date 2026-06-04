"""Unit tests for S&P 500 scorecard classification, verdict mapping, and screener pipeline."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from backend.daily_brief import (
    classify_company_preset,
    scorecard_verdict_mapping,
    run_sp500_screener_pipeline,
)
from backend.connectors.scorecard_data import ScorecardData


class TestScreenerPipeline(unittest.TestCase):
    def test_classify_company_preset_growth(self):
        self.assertEqual(classify_company_preset({"revenue_growth_pct": 18.5}), "growth")
        self.assertEqual(classify_company_preset({"revenue_growth_pct": 15.0, "dividend_yield_pct": 1.0}), "growth")

    def test_classify_company_preset_income(self):
        self.assertEqual(classify_company_preset({"revenue_growth_pct": 5.0, "dividend_yield_pct": 3.5}), "income")
        self.assertEqual(classify_company_preset({"dividend_yield_pct": 4.0}), "income")

    def test_classify_company_preset_value(self):
        self.assertEqual(classify_company_preset({"revenue_growth_pct": 10.0, "dividend_yield_pct": 2.0}), "value")
        self.assertEqual(classify_company_preset({}), "value")

    def test_scorecard_verdict_mapping(self):
        self.assertEqual(scorecard_verdict_mapping("Exceptional"), "Strong Buy")
        self.assertEqual(scorecard_verdict_mapping("Strong buy"), "Strong Buy")
        self.assertEqual(scorecard_verdict_mapping("Favorable"), "Buy")
        self.assertEqual(scorecard_verdict_mapping("Balanced"), "Hold")
        self.assertEqual(scorecard_verdict_mapping("Caution"), "Sell")
        self.assertEqual(scorecard_verdict_mapping("Avoid"), "Sell")

    @patch("backend.daily_brief._backend_type", return_value="none")
    @patch("backend.daily_brief.persist_snapshot", return_value=1)
    @patch("backend.daily_brief.apply_deep_verdicts", new_callable=AsyncMock)
    @patch("backend.connectors.scorecard_data.fetch_basket", new_callable=AsyncMock)
    @patch("backend.market_intel._get_sp500_universe", return_value=["AAPL", "MSFT", "T"])
    @patch("backend.daily_brief._fetch_all_symbols_from_db", return_value=[])
    def test_run_sp500_screener_pipeline(
        self,
        mock_fetch_db,
        mock_sp500_universe,
        mock_fetch_basket,
        mock_apply_deep,
        mock_persist,
        mock_backend_type,
    ):
        import asyncio

        # Mock fetch_basket return values
        mock_fetch_basket.return_value = [
            ScorecardData(
                ticker="AAPL",
                company_name="Apple",
                sector="Technology",
                industry="Consumer Electronics",
                current_price=180.0,
                forward_pe=28.0,
                historical_avg_pe=25.0,
                beta=1.2,
                eps_growth_pct=15.0,
                revenue_growth_pct=18.0,
                pt_upside_pct=12.0,
                dividend_yield_pct=0.5,
                debt_to_equity=1.5,
                ceo_name="Tim Cook",
                insider_buy_count_12m=0,
                insider_sell_count_12m=0,
                insider_net_shares_12m=0.0,
                held_percent_insiders=0.05,
                fields_missing=[],
            ),
            ScorecardData(
                ticker="MSFT",
                company_name="Microsoft",
                sector="Technology",
                industry="Software",
                current_price=400.0,
                forward_pe=35.0,
                historical_avg_pe=30.0,
                beta=1.0,
                eps_growth_pct=12.0,
                revenue_growth_pct=14.0,
                pt_upside_pct=8.0,
                dividend_yield_pct=0.8,
                debt_to_equity=0.5,
                ceo_name="Satya Nadella",
                insider_buy_count_12m=0,
                insider_sell_count_12m=0,
                insider_net_shares_12m=0.0,
                held_percent_insiders=0.01,
                fields_missing=[],
            ),
            ScorecardData(
                ticker="T",
                company_name="AT&T",
                sector="Telecom",
                industry="Telecom",
                current_price=18.0,
                forward_pe=8.0,
                historical_avg_pe=9.0,
                beta=0.7,
                eps_growth_pct=2.0,
                revenue_growth_pct=1.0,
                pt_upside_pct=15.0,
                dividend_yield_pct=6.5,
                debt_to_equity=1.2,
                ceo_name="John Stankey",
                insider_buy_count_12m=0,
                insider_sell_count_12m=0,
                insider_net_shares_12m=0.0,
                held_percent_insiders=0.02,
                fields_missing=[],
            ),
        ]

        # Mock apply_deep_verdicts to just return the rows unchanged
        mock_apply_deep.side_effect = lambda rows, llm: rows

        llm = MagicMock()
        payload = asyncio.run(run_sp500_screener_pipeline(date(2026, 5, 29), llm))

        self.assertEqual(len(payload["rows"]), 3)
        self.assertEqual(payload["trade_date"], "2026-05-29")
        self.assertEqual(payload["verdict_tier"], "deep")

        # Check preset classifications
        presets = {r["symbol"]: r["preset"] for r in payload["rows"]}
        self.assertEqual(presets["AAPL"], "growth")  # rev_growth = 18.0 >= 15.0
        self.assertEqual(presets["MSFT"], "value")   # default
        self.assertEqual(presets["T"], "income")     # div_yield = 6.5 >= 3.0

        # Check custom metric fields exist
        for r in payload["rows"]:
            self.assertIn("revenue_growth_pct", r)
            self.assertIn("eps_growth_pct", r)
            self.assertIn("dividend_yield_pct", r)
            self.assertIn("debt_to_equity", r)
            self.assertIn("beta", r)


if __name__ == "__main__":
    unittest.main()
