"""FinCrawlerClient.get_stock_news — HTTP behavior with mocked _get and scrape_text for fallbacks."""
import asyncio
import os
import unittest
from unittest.mock import patch

from backend.fincrawler_client import FinCrawlerClient, _cache


class TestFinCrawlerNews(unittest.TestCase):
    def setUp(self):
        self.env = {
            "FINCRAWLER_URL": "http://127.0.0.1:9",
            "FINCRAWLER_KEY": "test-key",
        }
        _cache.clear()

    def test_get_stock_news_fallback_long_lines(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            # Exception raised by _get to trigger the fallback logic
            async def fail_get(path, params=None):
                raise Exception("Forced failure")

            # Mock scrape_text to return lines of varying lengths
            async def fake_scrape_text(url, use_cache=True):
                return (
                    "Short line\n"
                    "This is a very long line that exceeds the sixty character limit constraint required to be extracted as a summary.\n"
                    "Another short line\n"
                    "Another line that is also quite long and definitely over the sixty character limit we are testing here.\n"
                    "   \n"
                    "Edge case exactly sixty characters exactly sixty characters! \n" # length 62 with space, length 60 without space. Strip will make it 60
                )

            async def go():
                with patch.object(c, "_get", side_effect=fail_get), \
                     patch.object(c, "scrape_text", side_effect=fake_scrape_text):
                    return await c.get_stock_news("AAPL", limit=2)

            res = asyncio.run(go())

            # Expected behavior:
            # - First short line skipped
            # - First long line extracted
            # - Second short line skipped
            # - Second long line extracted
            # - Empty line skipped
            # - Third long-ish line skipped because we only requested limit=2
            self.assertEqual(len(res), 2)
            self.assertTrue(res[0].startswith("This is a very long line"))
            self.assertTrue(res[1].startswith("Another line that is also quite long"))


if __name__ == "__main__":
    unittest.main()
