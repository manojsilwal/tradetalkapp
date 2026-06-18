"""
Truthful-data contract tests.

The app must never deliver a final result built on fabricated/placeholder
data: when a required live source fails, producers raise
InsufficientDataError (HTTP 503 via the handler in backend/main.py).
All tests are offline (mocked I/O).
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

from backend.data_errors import InsufficientDataError


class TestMacroConnectorTruthfulness(unittest.TestCase):
    def test_vix_failure_raises_instead_of_15_placeholder(self):
        from backend.connectors.macro import MacroHealthConnector

        conn = MacroHealthConnector()

        empty_hist = MagicMock()
        empty_hist.empty = True
        vix_ticker = MagicMock()
        vix_ticker.history.return_value = empty_hist

        with patch(
            "backend.connectors.quote_fallbacks._yahoo_chart_spot", return_value=None
        ), patch("yfinance.Ticker", return_value=vix_ticker):
            with self.assertRaises(InsufficientDataError) as ctx:
                asyncio.run(conn.fetch_data())
        self.assertIn("vix_level", ctx.exception.missing)


class TestShortsConnectorTruthfulness(unittest.TestCase):
    def test_yfinance_exception_raises(self):
        from backend.connectors.shorts import ShortsConnector
        import backend.connector_cache as cc

        cc._store.clear()
        conn = ShortsConnector()
        with patch("yfinance.Ticker", side_effect=RuntimeError("rate limited")):
            with self.assertRaises(InsufficientDataError):
                asyncio.run(conn.fetch_data(ticker="ZZZZ"))

    def test_missing_short_fields_raises(self):
        from backend.connectors.shorts import ShortsConnector
        import backend.connector_cache as cc

        cc._store.clear()
        inst = MagicMock()
        inst.info = {"longName": "No Shorts Inc"}
        conn = ShortsConnector()
        with patch("yfinance.Ticker", return_value=inst):
            with self.assertRaises(InsufficientDataError):
                asyncio.run(conn.fetch_data(ticker="ZZZY"))

    def test_real_short_data_still_returned(self):
        from backend.connectors.shorts import ShortsConnector
        import backend.connector_cache as cc

        cc._store.clear()
        inst = MagicMock()
        inst.info = {"shortPercentOfFloat": 0.18, "shortRatio": 3.4}
        conn = ShortsConnector()
        with patch("yfinance.Ticker", return_value=inst):
            data = asyncio.run(conn.fetch_data(ticker="ZZZX"))
        self.assertEqual(data["short_interest_ratio"], 18.0)
        self.assertEqual(data["days_to_cover"], 3.4)


class TestFundamentalsConnectorTruthfulness(unittest.TestCase):
    def test_yfinance_exception_raises(self):
        from backend.connectors.fundamentals import FundamentalsConnector
        import backend.connector_cache as cc

        cc._store.clear()
        conn = FundamentalsConnector()
        with patch("yfinance.Ticker", side_effect=RuntimeError("blocked")):
            with self.assertRaises(InsufficientDataError):
                asyncio.run(conn.fetch_data(ticker="ZZZW"))

    def test_missing_cash_and_debt_raises(self):
        from backend.connectors.fundamentals import FundamentalsConnector
        import backend.connector_cache as cc

        cc._store.clear()
        inst = MagicMock()
        inst.info = {"longName": "Thin Data Corp"}
        conn = FundamentalsConnector()
        with patch("yfinance.Ticker", return_value=inst):
            with self.assertRaises(InsufficientDataError):
                asyncio.run(conn.fetch_data(ticker="ZZZV"))


class TestSocialConnectorTruthfulness(unittest.TestCase):
    def test_rss_failure_degrades_gracefully(self):
        """After retries are exhausted the connector returns an empty-titles
        result with ``degraded=True`` instead of raising."""
        from backend.connectors.social import SocialSentimentConnector
        import backend.connector_cache as cc

        cc._store.clear()
        conn = SocialSentimentConnector()
        with patch("urllib.request.urlopen", side_effect=OSError("network down")), \
             patch("backend.connectors.social.fetch_youtube_titles_with_fallback", return_value=([], "none")), \
             patch("backend.connectors.social_sources.fetch_yfinance_news_titles", return_value=[]), \
             patch("backend.connectors.social_sources.fetch_youtube_channel_rss_titles", return_value=[]), \
             patch("backend.connectors.social_sources.fetch_reddit_titles", return_value=[]), \
             patch("backend.connectors.social_sources.fetch_stocktwits_titles", return_value=[]), \
             patch("backend.connectors.social._RSS_MAX_RETRIES", 0), \
             patch("backend.connectors.social._RSS_BACKOFF_BASE_S", 0.0):
            result = asyncio.run(conn.fetch_data(ticker="ZZZU"))
        self.assertTrue(result["degraded"])
        self.assertEqual(result["recent_titles"], [])
        self.assertEqual(result["counts"]["blogs"], 0)
        self.assertEqual(result["counts"]["youtube"], 0)


class TestPredictorTruthfulness(unittest.TestCase):
    def test_no_real_prices_returns_insufficient_data_status(self):
        from backend.predictor.agent import run_predictor_forecast

        with patch(
            "backend.predictor.agent._load_price_series_from_data_lake",
            return_value=None,
        ), patch("backend.predictor.agent.predictor_enabled", return_value=True):
            resp = asyncio.run(
                run_predictor_forecast("ZZZT", ["5d"], tool_registry=None, emit_ledger=False)
            )
        self.assertEqual(resp.status, "insufficient_data")
        self.assertFalse(resp.executed)
        self.assertEqual(resp.horizon_bands_usd, [])


class TestLLMClientTruthfulness(unittest.TestCase):
    def test_no_provider_verdict_role_raises(self):
        from backend.llm_client import LLMClient

        client = LLMClient()
        client._provider = "fallback"
        with self.assertRaises(InsufficientDataError):
            asyncio.run(client.generate("moderator", "verdict please"))

    def test_no_provider_nonverdict_role_uses_template(self):
        from backend.llm_client import LLMClient, FALLBACK_TEMPLATES

        client = LLMClient()
        client._provider = "fallback"
        result = asyncio.run(client.generate("video_scene_director", "scenes please"))
        self.assertEqual(result, FALLBACK_TEMPLATES["video_scene_director"])


class TestErrorPayloadShape(unittest.TestCase):
    def test_payload_fields(self):
        err = InsufficientDataError(
            "yfinance", "no data", ticker="aapl", missing=["price_history_6mo"]
        )
        payload = err.to_payload()
        self.assertEqual(payload["error"], "insufficient_data")
        self.assertEqual(payload["source"], "yfinance")
        self.assertEqual(payload["ticker"], "AAPL")
        self.assertEqual(payload["missing"], ["price_history_6mo"])


if __name__ == "__main__":
    unittest.main()
