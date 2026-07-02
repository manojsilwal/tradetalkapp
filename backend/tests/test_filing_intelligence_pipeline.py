"""Tests for filing intelligence pipeline and brain overlay."""
import os
import tempfile
import unittest

from backend.brain.filing_intelligence_scorer import (
    apply_revenue_quality_modifier,
    brain_features_from_record,
    compute_demand_visibility_score,
    compute_filing_group_score_0_100,
)
from backend.brain.filing_overlay import apply_filing_overlay
from backend.connectors.filing_intelligence import (
    build_risk_matrix,
    extract_heuristic_from_text,
    is_stale,
    upsert_filing_intelligence,
)
from backend.paper_portfolio import init_portfolio_db


class TestFilingIntelligenceScorer(unittest.TestCase):
    def test_demand_visibility_from_backlog(self):
        rec = {"order_backlog_usd": 20e9, "book_to_bill_ratio": 1.2}
        score = compute_demand_visibility_score(rec)
        self.assertIsNotNone(score)
        self.assertGreater(score, 0.5)

    def test_filing_group_score_range(self):
        feats = {
            "new_product_expansion_score": 0.8,
            "management_tone_score": 0.7,
            "filing_risk_score": 0.2,
            "demand_visibility_score": 0.75,
        }
        s = compute_filing_group_score_0_100(feats)
        self.assertGreaterEqual(s, 0)
        self.assertLessEqual(s, 100)

    def test_revenue_quality_penalizes_leveraged_growth(self):
        base = 65.0
        out = apply_revenue_quality_modifier(
            base,
            revenue_growth_yoy=0.5,
            gross_margin=0.12,
            debt_to_equity=320.0,
        )
        self.assertIsNotNone(out)
        self.assertLess(out, base)


class TestFilingHeuristicExtract(unittest.TestCase):
    def test_backlog_keyword_extraction(self):
        text = (
            "Our order backlog reached $19.6 billion, up 31% year over year. "
            "Data center revenue represented 30% of orders. Book-to-bill was 1.15."
        )
        rec = extract_heuristic_from_text("ETN", text)
        self.assertEqual(rec["ticker"], "ETN")
        self.assertIsNotNone(rec.get("order_backlog_usd"))
        self.assertTrue(rec.get("thematic_tags"))


class TestFilingOverlay(unittest.TestCase):
    def test_overlay_updates_filing_group(self):
        brain = {
            "live": {
                "signal_scores": {
                    "momentum": 50,
                    "quality": 60,
                    "valuation": 55,
                    "capital_flow": 50,
                    "filing_intelligence": 50,
                    "sentiment": 50,
                    "risk": 60,
                    "timeseries": 50,
                    "options_flow": 50,
                },
                "composite_score": 55,
            }
        }
        record = {
            "ticker": "ETN",
            "filing_risk_score": 0.2,
            "management_tone_score": 0.8,
            "new_product_expansion_score": 0.7,
            "demand_visibility_score": 0.85,
            "customer_concentration_score": 0.3,
        }
        out = apply_filing_overlay(brain, record)
        live = out["live"]
        self.assertGreater(live["signal_scores"]["filing_intelligence"], 50)


class TestFilingIntelligenceCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["PROGRESS_DB_PATH"] = os.path.join(self._tmpdir.name, "progress.db")
        init_portfolio_db()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_sqlite_upsert_and_read(self):
        rec = extract_heuristic_from_text(
            "BE",
            "Record backlog $20 billion. Customer concentration among hyperscalers. Data center 70%.",
        )
        upsert_filing_intelligence(rec)
        from backend.connectors.filing_intelligence import get_filing_intelligence

        loaded = get_filing_intelligence("BE")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["ticker"], "BE")
        self.assertFalse(is_stale(loaded))


class TestRiskMatrix(unittest.TestCase):
    def test_build_risk_matrix_keys(self):
        rec = {"customer_concentration_score": 0.7, "filing_risk_score": 0.6}
        rm = build_risk_matrix(rec, pe_ratio=45.0, debt_to_equity=120.0)
        self.assertIn("valuation", rm)
        self.assertIn("execution", rm)
        self.assertEqual(len(rm), 6)


if __name__ == "__main__":
    unittest.main()
