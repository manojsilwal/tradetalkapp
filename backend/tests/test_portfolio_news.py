"""
Unit tests for backend/routers/portfolio_news.py

Tests publisher whitelist filtering and response shape without hitting
yfinance or the LLM.
"""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestIsCrediblePublisher(unittest.TestCase):
    """Publisher whitelist filter logic."""

    def setUp(self):
        from backend.routers.portfolio_news import is_credible_publisher
        self.check = is_credible_publisher

    def test_known_credible_exact(self):
        for pub in ["Reuters", "Bloomberg", "CNBC", "MarketWatch", "Barron's"]:
            with self.subTest(publisher=pub):
                self.assertTrue(self.check(pub))

    def test_known_credible_substring(self):
        self.assertTrue(self.check("Reuters via Yahoo"))
        self.assertTrue(self.check("Bloomberg News"))
        self.assertTrue(self.check("Yahoo Finance Staff"))

    def test_credible_case_insensitive(self):
        self.assertTrue(self.check("REUTERS"))
        self.assertTrue(self.check("bloomberg"))

    def test_social_media_rejected(self):
        for pub in ["Reddit", "Twitter", "StockTwits", "TikTok", "Discord", ""]:
            with self.subTest(publisher=pub):
                self.assertFalse(self.check(pub))

    def test_empty_publisher_rejected(self):
        self.assertFalse(self.check(""))
        self.assertFalse(self.check(None))

    def test_unknown_source_rejected(self):
        self.assertFalse(self.check("CryptoMoonBoys"))
        self.assertFalse(self.check("InsiderTrades.io"))


class TestBuildNewsFeed(unittest.IsolatedAsyncioTestCase):
    """Integration-level tests for _build_news_feed with mocked yfinance + LLM."""

    def _make_yf_item(self, title, publisher, ts=1700000000):
        return {
            "title": title,
            "publisher": publisher,
            "link": "https://example.com/news",
            "providerPublishTime": ts,
        }

    async def test_response_shape(self):
        """Each returned item must have the expected keys and valid sentinel values."""
        from backend.routers.portfolio_news import _build_news_feed

        fake_news = [
            self._make_yf_item("AAPL posts record earnings", "Reuters"),
            self._make_yf_item("Ignore this post", "Reddit"),  # filtered out
        ]

        def _fake_yf_news(ticker):
            return fake_news

        fake_llm_result = {"sentiment": "positive", "impact": "Record earnings beat expectations."}

        with patch("backend.routers.portfolio_news._fetch_yf_news_sync", side_effect=_fake_yf_news):
            with patch("backend.routers.portfolio_news.llm_client") as mock_llm:
                mock_llm.generate = AsyncMock(return_value=fake_llm_result)
                items = await _build_news_feed(["AAPL"])

        self.assertEqual(len(items), 1)
        item = items[0]
        for key in ("ticker", "title", "publisher", "link", "published_at", "sentiment", "impact"):
            self.assertIn(key, item, f"Missing key: {key}")
        self.assertEqual(item["ticker"], "AAPL")
        self.assertEqual(item["sentiment"], "positive")
        self.assertEqual(item["impact"], "Record earnings beat expectations.")

    async def test_publisher_filter_drops_noise(self):
        """Only credible publishers survive the filter."""
        from backend.routers.portfolio_news import _build_news_feed

        fake_news = [
            self._make_yf_item("Real news", "Bloomberg"),
            self._make_yf_item("Noise post", "Random Blog"),
            self._make_yf_item("Another noise", "Twitter"),
        ]

        with patch("backend.routers.portfolio_news._fetch_yf_news_sync", return_value=fake_news):
            with patch("backend.routers.portfolio_news.llm_client") as mock_llm:
                mock_llm.generate = AsyncMock(return_value={"sentiment": "neutral", "impact": "ok"})
                items = await _build_news_feed(["TSLA"])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["publisher"], "Bloomberg")

    async def test_llm_failure_falls_back_gracefully(self):
        """LLM error must not drop the news item — fallback to neutral/null."""
        from backend.routers.portfolio_news import _build_news_feed

        fake_news = [self._make_yf_item("WSJ exclusive story", "Wall Street Journal")]

        with patch("backend.routers.portfolio_news._fetch_yf_news_sync", return_value=fake_news):
            with patch("backend.routers.portfolio_news.llm_client") as mock_llm:
                mock_llm.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
                items = await _build_news_feed(["MSFT"])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sentiment"], "neutral")
        self.assertIsNone(items[0]["impact"])

    async def test_deduplication_across_tickers(self):
        """Same headline from two tickers should appear only once."""
        from backend.routers.portfolio_news import _build_news_feed

        shared_item = self._make_yf_item("Fed raises rates", "Reuters")

        with patch("backend.routers.portfolio_news._fetch_yf_news_sync", return_value=[shared_item]):
            with patch("backend.routers.portfolio_news.llm_client") as mock_llm:
                mock_llm.generate = AsyncMock(return_value={"sentiment": "negative", "impact": "Rates up."})
                items = await _build_news_feed(["AAPL", "MSFT"])

        titles = [i["title"] for i in items]
        self.assertEqual(len(titles), len(set(titles)), "Duplicate headline was not removed")

    async def test_feed_capped_at_20(self):
        """Feed must never return more than 20 items."""
        from backend.routers.portfolio_news import _build_news_feed

        many_news = [
            self._make_yf_item(f"Reuters headline {i}", "Reuters", ts=1700000000 + i)
            for i in range(50)
        ]

        with patch("backend.routers.portfolio_news._fetch_yf_news_sync", return_value=many_news):
            with patch("backend.routers.portfolio_news.llm_client") as mock_llm:
                mock_llm.generate = AsyncMock(return_value={"sentiment": "neutral", "impact": "ok"})
                items = await _build_news_feed(["AAPL"])

        self.assertLessEqual(len(items), 20)

    async def test_empty_portfolio_returns_empty(self):
        """Zero tickers → empty items list."""
        from backend.routers.portfolio_news import _build_news_feed

        items = await _build_news_feed([])
        self.assertEqual(items, [])

    async def test_yfinance_error_returns_empty_for_ticker(self):
        """yfinance failure for one ticker must not crash the whole feed."""
        from backend.routers.portfolio_news import _build_news_feed

        def _boom(ticker):
            raise ConnectionError("yfinance unavailable")

        with patch("backend.routers.portfolio_news._fetch_yf_news_sync", side_effect=_boom):
            items = await _build_news_feed(["AAPL"])

        self.assertEqual(items, [])


class TestCacheKey(unittest.TestCase):
    def test_order_independent(self):
        from backend.routers.portfolio_news import _cache_key
        self.assertEqual(_cache_key(["AAPL", "MSFT"]), _cache_key(["MSFT", "AAPL"]))

    def test_normalizes_case(self):
        from backend.routers.portfolio_news import _cache_key
        self.assertEqual(_cache_key(["aapl"]), _cache_key(["AAPL"]))


if __name__ == "__main__":
    unittest.main()
