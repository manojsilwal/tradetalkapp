"""FinCrawlerClient.get_quote_price — HTTP behavior with mocked _get (no live FinCrawler)."""
import asyncio
import os
import unittest
from unittest.mock import patch

import httpx

from backend.fincrawler_client import FinCrawlerClient


class TestFinCrawlerQuote(unittest.TestCase):
    def setUp(self):
        self.env = {
            "FINCRAWLER_URL": "http://127.0.0.1:9",
            "FINCRAWLER_KEY": "test-key",
        }

    def test_get_quote_price_parses_ok_response(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            async def fake_get(path, params=None):
                self.assertEqual(path, "/quote")
                self.assertEqual(params, {"ticker": "AAPL"})
                return {"ok": True, "price": 250.25}

            async def go():
                with patch.object(c, "_get", side_effect=fake_get):
                    return await c.get_quote_price("AAPL")

            self.assertEqual(asyncio.run(go()), 250.25)

    def test_get_quote_price_returns_none_on_http_error(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None
            req = httpx.Request("GET", "http://127.0.0.1:9/quote")

            async def fail_get(path, params=None):
                raise httpx.HTTPStatusError(
                    "422",
                    request=req,
                    response=httpx.Response(422, request=req),
                )

            async def go():
                with patch.object(c, "_get", side_effect=fail_get):
                    return await c.get_quote_price("AAPL")

            self.assertIsNone(asyncio.run(go()))


if __name__ == "__main__":
    unittest.main()
