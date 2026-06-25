"""Scorecard route and connector timeouts (offline)."""
import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.connectors.scorecard_data import ScorecardData, _sync_fetch
from backend.data_errors import InsufficientDataError


def _sample_data(ticker: str = "AAPL") -> ScorecardData:
    return ScorecardData(
        ticker=ticker,
        company_name="Apple Inc.",
        sector="Technology",
        industry="Consumer Electronics",
        current_price=200.0,
        forward_pe=28.0,
        historical_avg_pe=25.0,
        beta=1.2,
        eps_growth_pct=10.0,
        revenue_growth_pct=8.0,
        pt_upside_pct=5.0,
        dividend_yield_pct=0.5,
        debt_to_equity=1.5,
        ceo_name="Tim Cook",
        insider_buy_count_12m=1,
        insider_sell_count_12m=2,
        insider_net_shares_12m=-100.0,
        held_percent_insiders=0.07,
        fields_missing=[],
    )


class TestScorecardRouteTimeout(unittest.TestCase):
    def setUp(self):
        os.environ["SCORECARD_FETCH_TIMEOUT_S"] = "0.2"

    def tearDown(self):
        os.environ.pop("SCORECARD_FETCH_TIMEOUT_S", None)

    def test_bounded_fetch_returns_within_deadline(self):
        from backend.routers import scorecard as sc

        async def _slow_fetch(_ticker: str):
            await asyncio.sleep(2.0)
            return _sample_data()

        with patch.object(sc, "fetch_scorecard_data", side_effect=_slow_fetch):
            t0 = time.perf_counter()
            with self.assertRaises(InsufficientDataError) as ctx:
                asyncio.run(sc._bounded_fetch_scorecard_data("AAPL"))
            elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.5)
        self.assertIn("timed out", str(ctx.exception.message).lower())
        self.assertIn("scorecard_fetch_timeout", ctx.exception.missing)

    def test_single_ticker_route_uses_bounded_fetch(self):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        sample = _sample_data()

        with patch(
            "backend.routers.scorecard._bounded_fetch_scorecard_data",
            new_callable=AsyncMock,
            return_value=sample,
        ), patch(
            "backend.routers.scorecard._fetch_subjective_scores",
            new_callable=AsyncMock,
            return_value=({"AAPL": {"sitg_score": 3.0, "archetype": ""}},
                            {"AAPL": {"exec_score": 5.0}},
                            {"AAPL": {"new_revenue_engine_score": 50.0}}),
        ), patch(
            "backend.routers.scorecard._fetch_verdicts_single",
            new_callable=AsyncMock,
            return_value={"AAPL": {"verdict": "Balanced", "one_line_reason": "ok"}},
        ):
            resp = client.get("/scorecard/AAPL?skip_llm_scores=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ticker"], "AAPL")


class TestScorecardPerCallTimeout(unittest.TestCase):
    def test_timed_call_returns_default_on_slow_call(self):
        from backend.connectors.scorecard_data import _timed_call
        import time

        def _slow():
            time.sleep(1.0)
            return "ok"

        out = _timed_call(_slow, timeout=0.05, default=None, label="slow.test")
        self.assertIsNone(out)

    def test_sync_fetch_degrades_when_nonessential_calls_time_out(self):
        info = {
            "longName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "currentPrice": 200.0,
            "forwardPE": 28.0,
            "beta": 1.2,
            "revenueGrowth": 0.08,
            "earningsGrowth": 0.1,
            "targetMeanPrice": 210.0,
            "debtToEquity": 150.0,
            "heldPercentInsiders": 0.07,
            "companyOfficers": [{"title": "CEO", "name": "Tim Cook"}],
        }

        def _timed_side(fn, *, timeout, default, label=""):
            if label.endswith(".info"):
                return info
            return default

        fake_yf = MagicMock()
        fake_yf.Ticker.return_value = MagicMock(info=info)

        with patch("backend.connectors.scorecard_data._timed_call", side_effect=_timed_side), patch.dict(
            "sys.modules", {"yfinance": fake_yf}
        ), patch("backend.connectors.spot.resolve_spot", return_value=None), patch(
            "backend.connectors.quote_fallbacks.fetch_us_equity_spot", return_value=None
        ):
            row = _sync_fetch("AAPL")

        self.assertEqual(row.ticker, "AAPL")
        self.assertIn("historical_avg_pe", row.fields_missing)


if __name__ == "__main__":
    unittest.main()
