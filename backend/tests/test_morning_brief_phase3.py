"""Phase 3 — GET /portfolio/morning-brief (Your Morning v0)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.morning_brief import (
    build_morning_brief,
    rank_card_candidates,
    _card_from_candidate,
    _select_cards,
)


class MorningBriefPhase3Base(unittest.TestCase):
    def setUp(self):
        from backend import paper_portfolio as pp
        from backend import portfolio_memory as pm

        self.pp = pp
        self.pm = pm
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "progress.db")
        pp.DB_PATH = self.db_path
        pm.DB_PATH = self.db_path
        for mod in (pp, pm):
            if hasattr(mod._local, "conn"):
                mod._local.conn.close()
                delattr(mod._local, "conn")
        pp.init_portfolio_db()

    def tearDown(self):
        for mod in (self.pp, self.pm):
            if hasattr(mod._local, "conn"):
                mod._local.conn.close()
                delattr(mod._local, "conn")
        self.tmp.cleanup()


class TestRankingLogic(unittest.TestCase):
    def test_nvda_impact_beats_tiny_mover(self):
        candidates = [
            {
                "symbol": "NVDA",
                "daily_return_pct": -2.0,
                "portfolio_weight": 0.30,
                "portfolio_impact_pct": -0.60,
                "user_interest_score": 0.5,
            },
            {
                "symbol": "TINY",
                "daily_return_pct": 8.0,
                "portfolio_weight": 0.02,
                "portfolio_impact_pct": 0.16,
                "user_interest_score": 0.5,
            },
        ]
        ranked = rank_card_candidates(candidates)
        self.assertEqual(ranked[0]["symbol"], "NVDA")

    def test_interest_does_not_override_large_impact(self):
        candidates = [
            {
                "symbol": "NVDA",
                "daily_return_pct": -3.0,
                "portfolio_weight": 0.40,
                "portfolio_impact_pct": -1.20,
                "user_interest_score": 0.2,
            },
            {
                "symbol": "MSFT",
                "daily_return_pct": 1.0,
                "portfolio_weight": 0.10,
                "portfolio_impact_pct": 0.10,
                "user_interest_score": 1.0,
            },
        ]
        ranked = rank_card_candidates(candidates)
        self.assertEqual(ranked[0]["symbol"], "NVDA")

    def test_macro_card_title_not_helped_today(self):
        card = _card_from_candidate(
            {
                "type": "macro_sector_watch",
                "symbol": None,
                "daily_return_pct": 0.0,
                "one_line_reason": "Your portfolio has 42% exposure to Technology.",
            },
            2,
        )
        self.assertEqual(card["title"], "What may matter to your portfolio")
        self.assertEqual(card["primary_metric"], "—")
        self.assertNotIn("helped your portfolio", card["title"].lower())

    def test_select_cards_prefers_neg_and_pos(self):
        ranked = rank_card_candidates([
            {"symbol": "A", "daily_return_pct": -2, "portfolio_weight": 0.5, "portfolio_impact_pct": -1},
            {"symbol": "B", "daily_return_pct": 2, "portfolio_weight": 0.3, "portfolio_impact_pct": 0.6},
            {"symbol": "C", "daily_return_pct": 0.1, "portfolio_weight": 0.1, "portfolio_impact_pct": 0.01,
             "type": "macro_sector_watch"},
        ])
        picked = _select_cards(ranked, max_cards=3)
        syms = [p["symbol"] for p in picked]
        self.assertIn("A", syms)
        self.assertIn("B", syms)


class TestMorningBriefAPI(MorningBriefPhase3Base):
    def test_empty_portfolio_response(self):
        brief = build_morning_brief("user_empty")
        self.assertFalse(brief["has_portfolio"])
        self.assertEqual(brief["cards"], [])
        self.assertIn("add your portfolio", brief["headline"].lower())

    def test_user_scoped_brief(self):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            with patch.object(self.pp, "_fetch_last_prices_batch", return_value={"NVDA": 100.0}):
                self.pp.add_position("user_a", "NVDA", "LONG", price=80, shares=10)
                self.pp.add_position("user_b", "MSFT", "LONG", price=400, shares=2)

        with patch("backend.morning_brief._movement_rows_for_symbols", return_value={
            "NVDA": {
                "daily_return_pct": -2.1,
                "one_line_reason": "Guidance concerns pressured semiconductors.",
                "primary_cause_category": "news",
            },
            "MSFT": {"daily_return_pct": 0.7, "one_line_reason": "AI optimism"},
        }):
            brief_a = build_morning_brief("user_a")
            brief_b = build_morning_brief("user_b")

        self.assertTrue(brief_a["has_portfolio"])
        self.assertTrue(brief_b["has_portfolio"])
        self.assertEqual(brief_a["user_id"], "user_a")
        self.assertEqual(brief_b["user_id"], "user_b")
        a_syms = {c.get("symbol") for c in brief_a["cards"]}
        self.assertIn("NVDA", a_syms)
        self.assertNotEqual(brief_a["summary"]["total_value"], brief_b["summary"]["total_value"])

    def test_max_three_cards(self):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            with patch.object(self.pp, "_fetch_last_prices_batch", return_value={"A": 10, "B": 10, "C": 10, "D": 10}):
                for t in ("A", "B", "C", "D"):
                    self.pp.add_position("u1", t, "LONG", price=10, shares=1)

        movement = {
            t: {"daily_return_pct": i + 1, "one_line_reason": f"move {t}"}
            for i, t in enumerate(["A", "B", "C", "D"])
        }
        with patch("backend.morning_brief._movement_rows_for_symbols", return_value=movement):
            brief = build_morning_brief("u1")
        self.assertLessEqual(len(brief["cards"]), 3)

    def test_memory_context_on_card(self):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            with patch.object(self.pp, "_fetch_last_prices_batch", return_value={"NVDA": 100.0}):
                self.pp.add_position("u1", "NVDA", "LONG", price=80, shares=10)

        with patch("backend.morning_brief._movement_rows_for_symbols", return_value={
            "NVDA": {"daily_return_pct": -2.0, "one_line_reason": "Semiconductor weakness"},
        }):
            brief = build_morning_brief("u1")

        self.assertTrue(brief["cards"])
        mem = brief["cards"][0].get("memory_context") or ""
        self.assertIn("since adding", mem.lower())


if __name__ == "__main__":
    unittest.main()
