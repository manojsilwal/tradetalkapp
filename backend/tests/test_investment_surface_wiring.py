"""Investment surface wiring: feature flag, serving passthrough, router gate,
and additive long-horizon grader horizons (offline)."""
import os
import unittest
from unittest.mock import patch

from backend.brain import serving


class TestFeatureFlag(unittest.TestCase):
    def _clear(self):
        for k in ("BRAIN_SERVE_ENABLE", "INVESTMENT_SURFACE"):
            os.environ.pop(k, None)

    def setUp(self):
        self._clear()

    def tearDown(self):
        self._clear()

    def test_disabled_by_default(self):
        self.assertFalse(serving.investment_surface_enabled())

    def test_requires_both_flags(self):
        os.environ["INVESTMENT_SURFACE"] = "1"
        self.assertFalse(serving.investment_surface_enabled())  # needs brain serving too
        os.environ["BRAIN_SERVE_ENABLE"] = "1"
        self.assertTrue(serving.investment_surface_enabled())


class TestServingPassthrough(unittest.TestCase):
    def test_no_snapshot_passthrough(self):
        with patch.object(serving, "serve_ticker",
                          return_value={"status": "no_snapshot", "ticker": "AAPL"}):
            out = serving.serve_investment_analysis("AAPL")
        self.assertEqual(out["status"], "no_snapshot")

    def test_wraps_live_result(self):
        fake = {
            "ticker": "AAPL", "status": "LIVE", "confidence_score": 0.7,
            "model_version": "v1", "reasons": [],
            "live": {"signal_scores": {"valuation": 80, "quality": 75, "risk": 70,
                                        "capital_flow": 60, "filing_intelligence": 65,
                                        "timeseries": 55, "momentum": 90, "sentiment": 50}},
            "valuation": {"base_price": 200.0, "live_price": 190.0,
                          "business_type": "wide_moat_compounder"},
            "business": {"business_type": "wide_moat_compounder"},
            "freshness": {"move_since_base": -0.05},
        }
        with patch.object(serving, "serve_ticker", return_value=fake):
            out = serving.serve_investment_analysis("AAPL")
        self.assertEqual(out["analysis_type"], "investment_research")
        self.assertEqual(out["minimum_horizon_months"], 12)
        self.assertIsNotNone(out["final"]["investment_score"])
        self.assertIn(out["final"]["stance"],
                      {"Strong Long-Term Buy", "Buy / Accumulate", "Accumulate Slowly",
                       "Hold / Watch", "Watchlist Only", "Speculative / High Risk", "Avoid"})


class TestRouterGate(unittest.TestCase):
    def test_analyze_company_503_when_disabled(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        for k in ("BRAIN_SERVE_ENABLE", "INVESTMENT_SURFACE"):
            os.environ.pop(k, None)
        r = client.get("/investment/analyze-company", params={"ticker": "AAPL"})
        self.assertEqual(r.status_code, 503)

    def test_health_always_available(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        r = client.get("/investment/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("investment_surface_enabled", r.json())


class TestGraderHorizonsAdditive(unittest.TestCase):
    def test_long_horizons_added_short_kept(self):
        from backend.outcome_grader import HORIZONS
        # short learning-loop horizons preserved (the self-learning heartbeat)
        for h in ("1d", "5d", "21d", "63d"):
            self.assertIn(h, HORIZONS)
        # long investment horizons added
        for h in ("252d", "756d"):
            self.assertIn(h, HORIZONS)


if __name__ == "__main__":
    unittest.main()
