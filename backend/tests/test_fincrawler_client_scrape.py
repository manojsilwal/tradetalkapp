"""FinCrawlerClient.scrape_text — HTTP behavior with mocked _post."""
import asyncio
import os
import time
import unittest
from unittest.mock import patch

import httpx

from backend.fincrawler_client import FinCrawlerClient, _cache


class TestFinCrawlerScrape(unittest.TestCase):
    def setUp(self):
        self.env = {
            "FINCRAWLER_URL": "http://127.0.0.1:9",
            "FINCRAWLER_KEY": "test-key",
        }
        _cache.clear()

    def test_scrape_text_not_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            c = FinCrawlerClient()
            self.assertEqual(asyncio.run(c.scrape_text("http://test.com")), "")

    def test_scrape_text_cache_hit(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None
            _cache["scrape:http://test.com"] = (time.time(), "cached content")

            async def go():
                with patch.object(c, "_post") as mock_post:
                    res = await c.scrape_text("http://test.com")
                    mock_post.assert_not_called()
                    return res

            self.assertEqual(asyncio.run(go()), "cached content")

    def test_scrape_text_success_markdown(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            async def fake_post(path, body):
                self.assertEqual(path, "/v1/scrape")
                self.assertEqual(body, {"url": "http://test.com", "formats": ["markdown"]})
                return {"success": True, "data": {"markdown": "Hello World"}}

            async def go():
                with patch.object(c, "_post", side_effect=fake_post):
                    return await c.scrape_text("http://test.com")

            self.assertEqual(asyncio.run(go()), "Hello World")
            self.assertIn("scrape:http://test.com", _cache)
            self.assertEqual(_cache["scrape:http://test.com"][1], "Hello World")

    def test_scrape_text_failure_response(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            async def fake_post(path, body):
                return {"success": False, "error": "Internal Error"}

            async def go():
                with patch.object(c, "_post", side_effect=fake_post):
                    return await c.scrape_text("http://test.com")

            self.assertEqual(asyncio.run(go()), "")
            self.assertNotIn("scrape:http://test.com", _cache)

    def test_scrape_text_legacy_format(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            async def fake_post(path, body):
                return {"text": "Legacy Content"}

            async def go():
                with patch.object(c, "_post", side_effect=fake_post):
                    return await c.scrape_text("http://test.com")

            self.assertEqual(asyncio.run(go()), "Legacy Content")

    def test_scrape_text_http_error(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None
            req = httpx.Request("POST", "http://127.0.0.1:9/v1/scrape")

            async def fail_post(path, body):
                raise httpx.HTTPStatusError(
                    "500",
                    request=req,
                    response=httpx.Response(500, request=req),
                )

            async def go():
                with patch.object(c, "_post", side_effect=fail_post):
                    return await c.scrape_text("http://test.com")

            self.assertEqual(asyncio.run(go()), "")


if __name__ == "__main__":
    unittest.main()
