"""YouTube API key fallback (primary → optional secondary) and RSS degradation."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.connectors.youtube_keys import (
    fetch_youtube_titles_with_fallback,
    probe_youtube_api_keys,
    youtube_api_key_candidates,
    youtube_search_titles,
)


class TestYouTubeKeyFallback(unittest.TestCase):
    _MANAGED = (
        "YOUTUBE_API_KEY",
        "youtube_api_key",
        "YOUTUBE_API_KEY_2",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    )

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self._MANAGED}
        for k in self._MANAGED:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k in self._MANAGED:
            if self._saved.get(k) is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self._saved[k]

    def test_candidates_skip_gemini_use_youtube_only(self) -> None:
        os.environ["YOUTUBE_API_KEY"] = "yt-key"
        os.environ["GEMINI_API_KEY"] = "gem-key"
        self.assertEqual(youtube_api_key_candidates(), ["yt-key"])

    def test_candidates_include_distinct_google_key(self) -> None:
        os.environ["YOUTUBE_API_KEY"] = "yt-key"
        os.environ["GOOGLE_API_KEY"] = "google-yt-key"
        self.assertEqual(youtube_api_key_candidates(), ["yt-key", "google-yt-key"])

    @patch("backend.connectors.youtube_keys.youtube_search_titles")
    def test_primary_fail_secondary_succeeds(self, mock_search) -> None:
        os.environ["YOUTUBE_API_KEY"] = "bad"
        os.environ["YOUTUBE_API_KEY_2"] = "good"

        mock_search.side_effect = [
            ([], "API key not valid"),
            (["AAPL video 1"], None),
        ]
        titles, source = fetch_youtube_titles_with_fallback("AAPL stock", limit=5)
        self.assertEqual(titles, ["AAPL video 1"])
        self.assertEqual(source, "youtube_api_v3_secondary")
        self.assertEqual(mock_search.call_count, 2)

    @patch("backend.connectors.youtube_keys.youtube_search_titles")
    def test_all_keys_fail_returns_none_source(self, mock_search) -> None:
        os.environ["YOUTUBE_API_KEY"] = "bad"
        mock_search.return_value = ([], "forbidden")

        titles, source = fetch_youtube_titles_with_fallback("AAPL stock", limit=5)
        self.assertEqual(titles, [])
        self.assertEqual(source, "none")

    @patch("backend.connectors.youtube_keys.youtube_search_titles")
    @patch("backend.connectors.social.SocialSentimentConnector._fetch_rss_titles")
    def test_probe_recommends_rss_when_api_fails(self, mock_rss, mock_search) -> None:
        os.environ["YOUTUBE_API_KEY"] = "bad"
        os.environ["GEMINI_API_KEY"] = "gem-key"
        mock_search.return_value = ([], "invalid key")
        mock_rss.return_value = ["RSS Title"]

        out = probe_youtube_api_keys("AAPL")
        self.assertEqual(out["recommended_path"], "rss")
        self.assertTrue(out["any_data"])
        self.assertIn("GEMINI_API_KEY is not used", out["gemini_key_note"] or "")


class TestYouTubeSearchParsing(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_parses_success_response(self, mock_urlopen) -> None:
        payload = {
            "items": [
                {"snippet": {"title": "Video 1"}},
                {"snippet": {"title": "Video 2"}},
            ]
        }
        mock_urlopen.return_value.read.return_value = json.dumps(payload).encode()

        titles, err = youtube_search_titles("test-key", "AAPL stock", limit=5)
        self.assertIsNone(err)
        self.assertEqual(titles, ["Video 1", "Video 2"])


if __name__ == "__main__":
    unittest.main()
