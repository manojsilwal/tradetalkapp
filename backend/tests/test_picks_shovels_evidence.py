"""Offline tests for the Phase-3 demand-evidence engine (deterministic + resilient)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.picks_shovels import evidence


class TestScoreKeywords(unittest.TestCase):
    def test_positive_language_scores_up(self):
        out = evidence.score_keywords([
            "Company reports record demand and raised guidance amid AI data center ramp",
            "Backlog hits all-time high as capacity expansion accelerates",
        ])
        self.assertGreater(out["positive_keyword_score"], 0)
        self.assertGreater(out["news_catalyst_score"], 0)
        self.assertEqual(out["negative_keyword_penalty"], 0)

    def test_negative_language_penalizes(self):
        out = evidence.score_keywords([
            "Firm warns of demand weakness and inventory correction, cuts guidance",
        ])
        self.assertGreater(out["negative_keyword_penalty"], 0)

    def test_neutral_text_is_zero(self):
        out = evidence.score_keywords(["The company will host its annual meeting next month"])
        self.assertEqual(out["positive_keyword_score"], 0)
        self.assertEqual(out["negative_keyword_penalty"], 0)
        self.assertEqual(out["news_catalyst_score"], 0)

    def test_empty_input(self):
        out = evidence.score_keywords([])
        self.assertEqual(out["positive_hits"], 0)
        self.assertEqual(out["negative_hits"], 0)

    def test_scores_are_bounded(self):
        spam = ["record demand backlog shortage capacity design win raised guidance"] * 50
        out = evidence.score_keywords(spam)
        self.assertLessEqual(out["positive_keyword_score"], 40.0)
        self.assertLessEqual(out["news_catalyst_score"], 20.0)


class TestFetchNewsEvidence(unittest.TestCase):
    def test_resilient_on_network_failure(self):
        with patch.object(evidence, "_google_news_rss", side_effect=RuntimeError("boom")):
            out = evidence.fetch_news_evidence("MU", "Micron")
        self.assertFalse(out["available"])

    def test_empty_results_unavailable(self):
        with patch.object(evidence, "_google_news_rss", return_value=[]):
            out = evidence.fetch_news_evidence("MU", "Micron")
        self.assertFalse(out["available"])

    def test_available_with_real_headlines(self):
        items = [
            {"title": "Micron sees record HBM demand, raised guidance", "link": "http://x", "pub_date": "", "source": "Reuters", "snippet": "AI data center capacity expansion"},
            {"title": "Analyst note on Micron", "link": "http://y", "pub_date": "", "source": "Blog", "snippet": "valuation update"},
        ]
        with patch.object(evidence, "_google_news_rss", return_value=items):
            out = evidence.fetch_news_evidence("MU", "Micron")
        self.assertTrue(out["available"])
        self.assertGreater(out["positive_keyword_score"], 0)
        self.assertGreaterEqual(len(out["demand_evidence"]), 1)
        # demand_evidence must be real headline text (no fabrication)
        self.assertIn("Micron sees record HBM demand", out["demand_evidence"][0])


class TestFetchDemandEvidence(unittest.TestCase):
    def test_filing_disabled_by_default(self):
        # With news off and filing off, merged result is unavailable.
        with patch.dict("os.environ", {"PICKS_SHOVELS_NEWS_EVIDENCE": "0", "PICKS_SHOVELS_FILING_EVIDENCE": "0"}):
            out = evidence.fetch_demand_evidence("MU", "Micron")
        self.assertFalse(out["available"])

    def test_merges_news(self):
        news = {
            "available": True,
            "positive_keyword_score": 24.0,
            "negative_keyword_penalty": 0.0,
            "news_catalyst_score": 10.0,
            "demand_evidence": ["Record demand — Reuters"],
            "headlines": [{"title": "Record demand", "link": "http://x", "source": "Reuters"}],
        }
        with patch.object(evidence, "news_evidence_enabled", return_value=True), \
             patch.object(evidence, "fetch_news_evidence", return_value=news), \
             patch.object(evidence, "fetch_filing_evidence", return_value={"available": False}):
            out = evidence.fetch_demand_evidence("MU", "Micron")
        self.assertTrue(out["available"])
        self.assertEqual(out["positive_keyword_score"], 24.0)
        self.assertEqual(out["filing_evidence_score"], 0.0)
        self.assertTrue(out["sources"]["news"])
        self.assertFalse(out["sources"]["filing"])


if __name__ == "__main__":
    unittest.main()
