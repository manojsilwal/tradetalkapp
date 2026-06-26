"""Offline tests for the deterministic explanation generator (anti-hallucination)."""
from __future__ import annotations

import unittest

from backend.picks_shovels import explain


def _row(*, with_data=True):
    fund = (
        {"revenue_growth_pct": 22.0, "gross_margin_pct": 60.0, "fcf_yield_pct": 4.0, "sector": "Technology"}
        if with_data else {"sector": "Technology"}
    )
    return {
        "ticker": "MU",
        "company_name": "Micron Technology",
        "themes": ["memory_hbm"],
        "bottleneck_solved": "High-bandwidth memory capacity.",
        "final_score": 88.0,
        "hiddenness_level": "Big Player",
        "confidence_level": "High",
        "score_breakdown": {"valuation_risk_score": 70.0},
        "fundamentals": fund,
        "momentum": {"ret_3m_pct": 12.0},
        "evidence": {"available": False},
    }


class TestExplain(unittest.TestCase):
    def test_has_required_sections(self):
        ex = explain.build_explanation(_row())
        for key in ("why_selected", "bottleneck_solved", "financial_evidence",
                    "demand_evidence", "risks", "confidence_level", "narrative"):
            self.assertIn(key, ex)

    def test_always_has_at_least_one_risk(self):
        ex = explain.build_explanation(_row())
        self.assertGreaterEqual(len(ex["risks"]), 1)

    def test_never_says_buy_or_sell(self):
        ex = explain.build_explanation(_row())
        low = ex["narrative"].lower()
        # word-boundary-ish checks: "selected" contains neither "buy" nor "sell"
        self.assertNotIn("buy", low)
        self.assertNotIn(" sell", low)
        self.assertIn("selected for research", low)

    def test_missing_data_renders_not_available(self):
        ex = explain.build_explanation(_row(with_data=False))
        joined = " ".join(ex["financial_evidence"])
        self.assertIn("Not available", joined)

    def test_cites_real_numbers_when_present(self):
        ex = explain.build_explanation(_row())
        joined = " ".join(ex["financial_evidence"])
        self.assertIn("22.0%", joined)
        self.assertIn("60.0%", joined)


if __name__ == "__main__":
    unittest.main()
