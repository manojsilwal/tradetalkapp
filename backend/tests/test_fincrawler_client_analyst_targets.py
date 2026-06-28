"""FinCrawlerClient.get_analyst_targets — mocked /quote/smart (no live FinCrawler)."""
import asyncio
import os
import unittest
from unittest.mock import patch

from backend.fincrawler_client import FinCrawlerClient, _cache


class TestFinCrawlerAnalystTargets(unittest.TestCase):
    def setUp(self):
        _cache.clear()
        self.env = {
            "FINCRAWLER_URL": "http://127.0.0.1:9",
            "FINCRAWLER_KEY": "test-key",
        }

    def test_get_analyst_targets_parses_quote_smart(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            async def fake_get(path, params=None):
                self.assertEqual(path, "/quote/smart")
                self.assertEqual(params["ticker"], "NVDA")
                return {
                    "ok": True,
                    "data": {
                        "targetMeanPrice": 210.0,
                        "targetHighPrice": 275.0,
                        "targetLowPrice": 150.0,
                        "targetMedianPrice": 205.0,
                        "numberOfAnalystOpinions": 52,
                        "recommendationMean": 1.8,
                        "recommendationKey": "buy",
                    },
                }

            async def go():
                with patch.object(c, "_get", side_effect=fake_get):
                    return await c.get_analyst_targets("NVDA")

            out = asyncio.run(go())
            self.assertEqual(out["mean_target_usd"], 210.0)
            self.assertEqual(out["high_target_usd"], 275.0)
            self.assertEqual(out["low_target_usd"], 150.0)
            self.assertEqual(out["num_analysts"], 52)
            self.assertEqual(out["recommendation_key"], "buy")
            self.assertEqual(out["source"], "fincrawler")

    def test_get_analyst_targets_returns_empty_without_mean(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            async def fake_get(path, params=None):
                return {"ok": True, "data": {"targetHighPrice": 300.0}}

            async def fake_scrape(url, use_cache=True):
                return "1y Target Est 195.00\nNo. of Analyst Opinions 48"

            async def go():
                with patch.object(c, "_get", side_effect=fake_get):
                    with patch.object(c, "scrape_text", side_effect=fake_scrape):
                        return await c.get_analyst_targets("NVDA")

            out = asyncio.run(go())
            self.assertEqual(out["mean_target_usd"], 195.0)
            self.assertEqual(out["num_analysts"], 48)
            self.assertEqual(out["source"], "yahoo_analysis_scrape")

    def test_get_analyst_targets_sync_parses_ok_response(self):
        with patch.dict(os.environ, self.env, clear=False):
            c = FinCrawlerClient()
            c._enabled = None

            class _Resp:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "ok": True,
                        "data": {
                            "targetMeanPrice": 188.5,
                            "targetHighPrice": 220.0,
                            "targetLowPrice": 140.0,
                        },
                    }

            class _Client:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *args):
                    return False

                def get(self_inner, url, headers=None, params=None):
                    self.assertIn("/quote/smart", url)
                    return _Resp()

            with patch("httpx.Client", return_value=_Client()):
                out = c.get_analyst_targets_sync("AAPL")
            self.assertEqual(out["mean_target_usd"], 188.5)


if __name__ == "__main__":
    unittest.main()
