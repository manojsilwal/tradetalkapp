"""Unit tests for free social sentiment source fetchers (offline mocks)."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from backend.connectors import social_sources
from backend.connectors.social import SocialSentimentConnector


class TestSocialSources(unittest.TestCase):
    _MANAGED = ("SOCIAL_ENABLE_REDDIT", "SOCIAL_ENABLE_STOCKTWITS")

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self._MANAGED}

    def tearDown(self) -> None:
        for k in self._MANAGED:
            if self._saved.get(k) is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self._saved[k]

    @patch("yfinance.Ticker")
    def test_yfinance_news_titles(self, mock_ticker_cls) -> None:
        mock_ticker_cls.return_value.news = [
            {"title": "Apple beats estimates"},
            {"content": {"title": "AAPL rises on iPhone demand"}},
        ]
        out = social_sources.fetch_yfinance_news_titles("AAPL", limit=5)
        self.assertEqual(len(out), 2)
        self.assertIn("Apple", out[0])

    @patch("backend.connectors.social_sources._http_xml")
    def test_youtube_channel_rss_filters_ticker(self, mock_xml) -> None:
        atom = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>AAPL stock outlook for 2026</title></entry>
          <entry><title>Macro week ahead</title></entry>
        </feed>"""
        mock_xml.return_value = atom.encode()
        with patch("backend.connectors.youtube.FINANCE_CHANNELS", [{"id": "UCtest", "name": "Test"}]):
            out = social_sources.fetch_youtube_channel_rss_titles("AAPL", limit=5)
        self.assertTrue(out)
        self.assertIn("AAPL", out[0])

    @patch("backend.connectors.social_sources._http_json")
    def test_reddit_titles(self, mock_json) -> None:
        mock_json.return_value = {
            "data": {
                "children": [
                    {"data": {"title": "Why AAPL is breaking out"}},
                    {"data": {"title": "Unrelated post"}},
                ]
            }
        }
        out = social_sources.fetch_reddit_titles("AAPL", limit=5)
        self.assertEqual(len(out), 1)
        self.assertIn("AAPL", out[0])

    @patch("backend.connectors.social_sources._http_json")
    def test_stocktwits_titles(self, mock_json) -> None:
        mock_json.return_value = {
            "messages": [
                {"body": "$AAPL looking strong into earnings"},
                {"body": "Bullish on Apple here"},
            ]
        }
        out = social_sources.fetch_stocktwits_titles("AAPL", limit=5)
        self.assertEqual(len(out), 2)


class TestSocialConnectorMerge(unittest.TestCase):
    def test_fetch_data_merges_sources(self) -> None:
        import backend.connector_cache as cc

        cc._store.clear()
        conn = SocialSentimentConnector()
        with patch.object(
            SocialSentimentConnector,
            "_resolve_youtube_titles",
            return_value=(["YT title"], "youtube_api_v3"),
        ), patch.object(
            SocialSentimentConnector,
            "_fetch_rss_titles",
            return_value=["Blog headline"],
        ), patch(
            "backend.connectors.social_sources.fetch_yfinance_news_titles",
            return_value=["YF headline"],
        ), patch(
            "backend.connectors.social_sources.fetch_reddit_titles",
            return_value=["Reddit post"],
        ), patch(
            "backend.connectors.social_sources.fetch_stocktwits_titles",
            return_value=["$AAPL moon"],
        ):
            result = asyncio.run(conn.fetch_data(ticker="AAPL"))

        self.assertFalse(result["degraded"])
        self.assertEqual(len(result["recent_titles"]), 5)
        self.assertEqual(result["counts"]["youtube"], 1)
        self.assertEqual(result["counts"]["blogs"], 1)
        self.assertEqual(result["counts"]["yfinance_news"], 1)
        self.assertEqual(result["counts"]["reddit"], 1)
        self.assertEqual(result["counts"]["stocktwits"], 1)


if __name__ == "__main__":
    unittest.main()
