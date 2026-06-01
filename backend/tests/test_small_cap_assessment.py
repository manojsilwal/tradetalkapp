"""Offline tests for small-cap growth-stage assessment."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas import SmallCapAssessment, SmallCapSignal
from backend.routers import small_cap as small_cap_router


SMALL_CAP_FIXTURE = {
    "ticker": "SMCAP",
    "market_cap": 900_000_000,
    "cap_bucket": "Small Cap",
    "sector": "Technology",
    "industry": "Software",
    "long_business_summary": "Builds workflow automation for mid-market manufacturers.",
    "revenue_yoy": [
        {"period": "2024-12-31", "value": 120_000_000, "yoy_growth_pct": 28.5},
        {"period": "2023-12-31", "value": 93_000_000, "yoy_growth_pct": 22.0},
    ],
    "gross_margins_quarterly": [
        {"period": "2025-03-31", "margin_pct": 62.0},
        {"period": "2024-12-31", "margin_pct": 58.0},
    ],
    "institutional_holders": [
        {"name": "Vanguard Group", "pct_held": 4.2},
        {"name": "BlackRock", "pct_held": 3.1},
    ],
    "institutional_ownership_pct": 31.5,
    "officers": [
        {"name": "Jane Founder", "title": "Chief Executive Officer & Founder", "age": 42},
        {"name": "Alex Ops", "title": "Chief Operating Officer", "age": 39},
    ],
    "net_income": -12_000_000,
    "forward_eps": 0.45,
    "trailing_eps": -0.32,
    "revenue_growth_yoy_pct": 28.5,
    "company_revenue_history_5y": [
        {"year": "2022", "revenue_usd": 70_000_000, "gross_margin_pct": 55.0, "operating_margin_pct": -8.0},
        {"year": "2023", "revenue_usd": 93_000_000, "gross_margin_pct": 58.0, "operating_margin_pct": -5.0},
        {"year": "2024", "revenue_usd": 120_000_000, "gross_margin_pct": 62.0, "operating_margin_pct": -2.0},
    ],
    "segment_revenue_streams": [
        {
            "name": "Software",
            "years": [
                {"year": "2023", "revenue_usd": 60_000_000},
                {"year": "2024", "revenue_usd": 78_000_000},
            ],
        }
    ],
    "news_headlines": [
        {
            "title": "SMCAP signs $40M multi-year contract with Fortune 500 manufacturer",
            "publisher": "Reuters",
            "source": "yfinance",
        }
    ],
}


LLM_FIXTURE = {
    "signals": [
        {
            "label": label,
            "score": "green" if i < 3 else "yellow",
            "headline": f"{label} headline",
            "detail": f"{label} detail text.",
        }
        for i, label in enumerate(small_cap_router._REQUIRED_LABELS)
    ],
    "overall_verdict": "Compelling",
    "overall_rationale": "Strong growth-stage profile with credible near-term path.",
    "revenue_streams": [
        {
            "name": "Automation Software",
            "latest_share_pct": 65.0,
            "years": [
                {"year": "2024", "revenue_usd": 120_000_000, "gross_margin_pct": 62.0, "operating_margin_pct": -2.0}
            ],
        }
    ],
    "major_deals": [
        {
            "partner": "Fortune 500 Manufacturer",
            "deal_type": "customer contract",
            "amount_usd": 40_000_000,
            "amount_label": "$40M multi-year",
            "year": 2025,
            "summary": "Multi-year automation rollout.",
            "predictability_note": "Backlog visibility for next 3 years.",
        }
    ],
}


class TestSmallCapSchemas(unittest.TestCase):
    def test_small_cap_assessment_validates(self):
        payload = SmallCapAssessment(
            ticker="SMCAP",
            cap_bucket="Small Cap",
            signals=[
                SmallCapSignal(
                    label=label,
                    score="yellow",
                    headline="h",
                    detail="d",
                )
                for label in small_cap_router._REQUIRED_LABELS
            ],
            overall_verdict="Watch",
            overall_rationale="Mixed signals.",
        )
        self.assertEqual(len(payload.signals), 6)
        self.assertEqual(payload.ticker, "SMCAP")


class TestSmallCapHeuristics(unittest.TestCase):
    def test_build_heuristic_signals_returns_six_labels(self):
        signals = small_cap_router._build_heuristic_signals(SMALL_CAP_FIXTURE)
        labels = [s.label for s in signals]
        self.assertEqual(labels, list(small_cap_router._REQUIRED_LABELS))
        for sig in signals:
            self.assertIn(sig.score, {"green", "yellow", "red"})


class TestSmallCapAssessmentRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.routers.small_cap.small_cap_metrics_connector.fetch_data", new_callable=AsyncMock)
    @patch("backend.routers.small_cap.llm_client.generate_small_cap_analysis", new_callable=AsyncMock)
    def test_success_returns_six_signals(self, mock_llm, mock_fetch):
        mock_fetch.return_value = dict(SMALL_CAP_FIXTURE)
        mock_llm.return_value = dict(LLM_FIXTURE)

        resp = self.client.get("/small-cap-assessment/SMCAP")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["ticker"], "SMCAP")
        self.assertEqual(body["cap_bucket"], "Small Cap")
        self.assertEqual(len(body["signals"]), 6)
        self.assertEqual(body["overall_verdict"], "Compelling")
        labels = [s["label"] for s in body["signals"]]
        self.assertEqual(labels, list(small_cap_router._REQUIRED_LABELS))
        self.assertGreaterEqual(len(body.get("revenue_streams", [])), 1)
        self.assertGreaterEqual(len(body.get("major_deals", [])), 1)

    def test_baseline_revenue_streams_from_connector(self):
        streams = small_cap_router._baseline_revenue_streams(SMALL_CAP_FIXTURE)
        names = [s.name for s in streams]
        self.assertIn("Total Company", names)
        self.assertIn("Software", names)
        total = next(s for s in streams if s.name == "Total Company")
        self.assertIn("Yahoo Finance", total.source or "")

    def test_news_fallback_major_deals(self):
        deals = small_cap_router._normalize_major_deals([], SMALL_CAP_FIXTURE)
        self.assertGreaterEqual(len(deals), 1)
        self.assertIn("contract", deals[0].summary.lower())
        self.assertIn("Reuters", deals[0].source or "")

    def test_fincrawler_news_fallback_major_deals(self):
        data = {
            **SMALL_CAP_FIXTURE,
            "news_headlines": [],
            "fincrawler_news_summaries": [
                "SMCAP announces strategic partnership with global cloud provider"
            ],
        }
        deals = small_cap_router._normalize_major_deals([], data)
        self.assertGreaterEqual(len(deals), 1)
        self.assertIn("partnership", deals[0].summary.lower())
        self.assertEqual(deals[0].source, "FinCrawler news")

    def test_merge_news_headlines_dedupes(self):
        from backend.connectors.small_cap_metrics import _merge_news_headlines

        merged = _merge_news_headlines(
            [{"title": "Deal signed", "publisher": "Yahoo", "source": "yfinance"}],
            [{"title": "Deal signed", "summary": "extra", "source": "fincrawler"}],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "Deal signed")

    @patch("backend.connectors.small_cap_metrics._fetch_fincrawler_enrichment", new_callable=AsyncMock)
    @patch("backend.connectors.small_cap_metrics._fetch_small_cap_bundle")
    def test_fetch_data_merges_fincrawler(self, mock_yf, mock_fc):
        from backend.connectors.small_cap_metrics import SmallCapMetricsConnector

        mock_yf.return_value = {
            **SMALL_CAP_FIXTURE,
            "news_headlines": [{"title": "YF headline", "publisher": "Yahoo"}],
        }
        mock_fc.return_value = {
            "fincrawler_enabled": True,
            "fincrawler_sec_10k_excerpt": "Segment revenue table...",
            "fincrawler_sec_10q_excerpt": "",
            "fincrawler_sec_8k_excerpt": "",
            "fincrawler_news_summaries": ["FC summary"],
            "fincrawler_news_articles": [
                {"title": "FC headline", "summary": "details", "source": "fincrawler"}
            ],
        }

        async def _run():
            conn = SmallCapMetricsConnector()
            return await conn.fetch_data(ticker="SMCAP")

        import asyncio

        result = asyncio.run(_run())
        self.assertEqual(result.get("fincrawler_sec_10k_excerpt"), "Segment revenue table...")
        titles = [h.get("title") for h in result.get("news_headlines") or []]
        self.assertIn("YF headline", titles)
        self.assertIn("FC headline", titles)

    @patch("backend.routers.small_cap.small_cap_metrics_connector.fetch_data", new_callable=AsyncMock)
    def test_large_cap_rejected(self, mock_fetch):
        mock_fetch.return_value = {
            **SMALL_CAP_FIXTURE,
            "ticker": "AAPL",
            "market_cap": 3_000_000_000_000,
            "cap_bucket": "Mega Cap",
        }

        resp = self.client.get("/small-cap-assessment/AAPL")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Small Cap", resp.json()["detail"])

    @patch("backend.routers.small_cap.small_cap_metrics_connector.fetch_data", new_callable=AsyncMock)
    def test_mid_cap_rejected(self, mock_fetch):
        mock_fetch.return_value = {
            **SMALL_CAP_FIXTURE,
            "market_cap": 5_000_000_000,
            "cap_bucket": "Mid Cap",
        }

        resp = self.client.get("/small-cap-assessment/MID")
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
