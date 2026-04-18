"""HTTP integration tests for /scorecard/* with stubbed connector + LLM."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient  # noqa: E402

from backend.connectors.scorecard_data import ScorecardData  # noqa: E402
from backend.main import app  # noqa: E402


def _fake_row(ticker: str, **overrides) -> ScorecardData:
    base = dict(
        ticker=ticker,
        company_name=f"{ticker} Inc",
        sector="Industrials",
        industry="Electrical Equipment",
        current_price=100.0,
        forward_pe=25.0,
        historical_avg_pe=20.0,
        beta=1.2,
        eps_growth_pct=10.0,
        revenue_growth_pct=8.0,
        pt_upside_pct=5.0,
        dividend_yield_pct=1.0,
        debt_to_equity=0.6,
        ceo_name=f"CEO of {ticker}",
        insider_buy_count_12m=1,
        insider_sell_count_12m=0,
        insider_net_shares_12m=500.0,
        held_percent_insiders=0.05,
        fields_missing=[],
    )
    base.update(overrides)
    return ScorecardData(**base)


async def _fake_fetch_basket(tickers):
    return [_fake_row(t) for t in tickers]


async def _fake_fetch_single(ticker):
    return _fake_row(ticker)


class TestScorecardRouterPresets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_presets_endpoint_returns_four(self):
        r = self.client.get("/scorecard/presets")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(
            set(body.keys()), {"growth", "value", "income", "balanced"}
        )
        # Each preset has all nine weights w1..w9.
        for name, weights in body.items():
            self.assertEqual(
                set(weights.keys()),
                {"w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9"},
                f"preset {name} is missing weights",
            )


class TestScorecardCompare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_compare_with_skip_llm_scores(self):
        """Stub connector only — skip_llm_scores avoids any real LLM call."""
        with patch(
            "backend.routers.scorecard.fetch_basket", side_effect=_fake_fetch_basket
        ):
            r = self.client.post(
                "/scorecard/compare",
                json={
                    "tickers": ["AAA", "BBB", "CCC"],
                    "preset": "balanced",
                    "skip_llm_scores": True,
                },
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["preset"], "balanced")
        self.assertEqual(len(body["rows"]), 3)
        tickers = [row["ticker"] for row in body["rows"]]
        self.assertEqual(set(tickers), {"AAA", "BBB", "CCC"})
        for row in body["rows"]:
            self.assertIn(row["signal"], {
                "Exceptional", "Strong buy", "Favorable",
                "Balanced", "Caution", "Avoid",
            })
            # skip_llm → verdict falls through the local mapper.
            self.assertIn(row["verdict"], {
                "Strong", "Favorable", "Balanced", "Stretched", "Avoid",
            })
            # sub-scores present and in range.
            self.assertGreaterEqual(row["return_score"]["weighted"], 0.0)
            self.assertLessEqual(row["return_score"]["weighted"], 10.0)
            self.assertGreaterEqual(row["risk_score"]["weighted"], 0.0)
            self.assertLessEqual(row["risk_score"]["weighted"], 10.0)

    def test_compare_rejects_unknown_preset(self):
        r = self.client.post(
            "/scorecard/compare",
            json={"tickers": ["AAA"], "preset": "contrarian", "skip_llm_scores": True},
        )
        self.assertEqual(r.status_code, 422)

    def test_compare_rejects_empty_tickers(self):
        r = self.client.post(
            "/scorecard/compare",
            json={"tickers": [], "preset": "balanced", "skip_llm_scores": True},
        )
        self.assertEqual(r.status_code, 422)

    def test_compare_tickers_are_uppercased_and_deduped(self):
        with patch(
            "backend.routers.scorecard.fetch_basket", side_effect=_fake_fetch_basket
        ):
            r = self.client.post(
                "/scorecard/compare",
                json={
                    "tickers": ["aaa", "AAA", "bbb", "Ccc"],
                    "preset": "growth",
                    "skip_llm_scores": True,
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["rows"]), 3)
        self.assertEqual(
            sorted(row["ticker"] for row in body["rows"]),
            ["AAA", "BBB", "CCC"],
        )

    def test_compare_situational_flag_changes_weights(self):
        """bear_or_rate_hike doubles w6 (beta weight) — check it lands in response."""
        with patch(
            "backend.routers.scorecard.fetch_basket", side_effect=_fake_fetch_basket
        ):
            r = self.client.post(
                "/scorecard/compare",
                json={
                    "tickers": ["AAA"],
                    "preset": "balanced",
                    "situational_flags": {"bear_or_rate_hike": True},
                    "skip_llm_scores": True,
                },
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # balanced preset has w6=2; flag multiplies by 2 → 4.
        self.assertEqual(body["weights"]["w6"], 4.0)

    def test_compare_surfaces_missing_field_notes(self):
        async def _fake_with_missing(tickers):
            return [_fake_row(t, fields_missing=["forward_pe"]) for t in tickers]

        with patch(
            "backend.routers.scorecard.fetch_basket", side_effect=_fake_with_missing
        ):
            r = self.client.post(
                "/scorecard/compare",
                json={
                    "tickers": ["AAA"],
                    "preset": "balanced",
                    "skip_llm_scores": True,
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(any("forward_pe" in n for n in body["notes"]))


class TestScorecardSingleTicker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_single_ticker_returns_row(self):
        with patch(
            "backend.routers.scorecard.fetch_scorecard_data",
            side_effect=_fake_fetch_single,
        ):
            r = self.client.get("/scorecard/AAPL?preset=balanced&skip_llm_scores=true")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["ticker"], "AAPL")
        self.assertIn("return_score", body)
        self.assertIn("risk_score", body)
        self.assertIn("ratio", body)

    def test_single_rejects_invalid_preset(self):
        r = self.client.get("/scorecard/AAPL?preset=nope")
        self.assertEqual(r.status_code, 400)

    def test_single_rejects_invalid_ticker(self):
        r = self.client.get("/scorecard/TOOLONGTICKER?preset=balanced")
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
