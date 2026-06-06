"""Phase 3 — GET /portfolio/morning-brief (Your Morning v0)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.morning_brief import (
    build_morning_brief,
    rank_card_candidates,
    _build_candidates_from_positions,
    _card_from_candidate,
    _continue_where_you_left_off,
    _looks_like_pnl_not_session,
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

    def test_macro_card_uses_sector_visual_fields(self):
        card = _card_from_candidate(
            {
                "type": "macro_sector_watch",
                "symbol": None,
                "sector_name": "Technology",
                "allocation_pct": 42.0,
                "daily_return_pct": 0.0,
                "one_line_reason": "Your portfolio has 42% exposure to Technology.",
            },
            2,
        )
        self.assertEqual(card["title"], "Technology")
        self.assertEqual(card["primary_metric"], "42%")
        self.assertEqual(card["chip"], "EXPOSURE")
        self.assertNotIn("helped your portfolio", (card.get("title") or "").lower())

    def test_movement_daily_matching_pnl_is_rejected(self):
        self.assertTrue(_looks_like_pnl_not_session(341.8, 341.8))
        self.assertFalse(_looks_like_pnl_not_session(-9.6, 341.8))

    def test_daily_returns_reject_pnl_masquerading_as_daily(self):
        from datetime import date
        from unittest.mock import patch

        from backend.morning_brief import _daily_returns_for_symbols

        trade_date = date(2026, 6, 5)
        movement = {"MRVL": {"daily_return_pct": 341.8, "one_line_reason": "bad data"}}
        with patch(
            "backend.morning_brief._fetch_daily_returns_batch",
            return_value={"MRVL": -8.2},
        ):
            out = _daily_returns_for_symbols(
                ["MRVL"], movement, trade_date, pnl_by_symbol={"MRVL": 341.8}
            )
        self.assertEqual(out["MRVL"], -8.2)

    def test_continue_prefers_featured_symbols_over_stale_clicks(self):
        with patch(
            "backend.morning_brief.pm.list_user_actions",
            return_value=[{"symbol": "MSFT"}],
        ):
            cont = _continue_where_you_left_off(
                "u1",
                ["MSFT", "MRVL", "SIVR"],
                featured_symbols=["SIVR", "MRVL"],
            )
        self.assertEqual(cont["symbol"], "SIVR")

    def test_continue_absent_when_no_featured_match(self):
        with patch(
            "backend.morning_brief.pm.list_user_actions",
            return_value=[{"symbol": "MSFT"}],
        ):
            cont = _continue_where_you_left_off("u1", ["AAPL"], featured_symbols=["MRVL"])
        self.assertIsNone(cont)

    def test_pnl_pct_never_used_as_daily_metric(self):
        positions = [{
            "ticker": "MRVL",
            "current_value": 1000,
            "pnl_pct": 341.8,
            "entry_date": "2026-05-30",
        }]
        daily_returns = {"MRVL": -12.5}
        movement = {"MRVL": {"daily_return_pct": -12.5, "one_line_reason": "Chip selloff"}}
        cands = _build_candidates_from_positions("u1", positions, 1000, movement, daily_returns)
        card = _card_from_candidate(cands[0], 0)
        self.assertEqual(card["chip"], "DRAG")
        self.assertEqual(card["primary_metric"], "-12.5%")
        self.assertNotIn("341", card["primary_metric"])

    def test_select_cards_on_down_day_prefers_draggers(self):
        ranked = rank_card_candidates([
            {
                "symbol": "SIVR",
                "daily_return_pct": -9.6,
                "daily_verified": True,
                "portfolio_impact_pct": -0.5,
                "portfolio_weight": 0.05,
            },
            {
                "symbol": "MRVL",
                "daily_return_pct": -12.0,
                "daily_verified": True,
                "portfolio_impact_pct": -0.8,
                "portfolio_weight": 0.06,
            },
            {
                "symbol": "AAPL",
                "daily_return_pct": 0.3,
                "daily_verified": True,
                "portfolio_impact_pct": 0.01,
                "portfolio_weight": 0.3,
            },
        ])
        picked = _select_cards(ranked, portfolio_daily_pct=-5.4, max_cards=2)
        syms = [p["symbol"] for p in picked if p.get("symbol")]
        self.assertIn("MRVL", syms)
        self.assertIn("SIVR", syms)


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

        movement = {
            "NVDA": {
                "daily_return_pct": -2.1,
                "one_line_reason": "Guidance concerns pressured semiconductors.",
                "primary_cause_category": "news",
            },
            "MSFT": {"daily_return_pct": 0.7, "one_line_reason": "AI optimism"},
        }
        with patch("backend.morning_brief._movement_rows_for_symbols", return_value=movement):
            with patch(
                "backend.morning_brief._daily_returns_for_symbols",
                return_value={"NVDA": -2.1, "MSFT": 0.7},
            ):
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
        daily = {t: float(i + 1) for i, t in enumerate(["A", "B", "C", "D"])}
        with patch("backend.morning_brief._movement_rows_for_symbols", return_value=movement):
            with patch("backend.morning_brief._daily_returns_for_symbols", return_value=daily):
                brief = build_morning_brief("u1")
        self.assertLessEqual(len(brief["cards"]), 3)

    def test_watch_next_omitted_when_macro_card_present(self):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            with patch.object(self.pp, "_fetch_last_prices_batch", return_value={"NVDA": 100.0, "MSFT": 400.0}):
                self.pp.add_position("u1", "NVDA", "LONG", price=80, shares=50)
                self.pp.add_position("u1", "MSFT", "LONG", price=400, shares=5)

        movement = {
            "NVDA": {"daily_return_pct": -2.0, "one_line_reason": "Semiconductor weakness"},
            "MSFT": {"daily_return_pct": -1.0, "one_line_reason": "Tech drift"},
        }
        with patch("backend.morning_brief._movement_rows_for_symbols", return_value=movement):
            with patch("backend.morning_brief._daily_returns_for_symbols", return_value={"NVDA": -2.0, "MSFT": -1.0}):
                brief = build_morning_brief("u1")

        has_macro = any(c.get("type") == "macro_sector_watch" for c in brief.get("cards") or [])
        if has_macro:
            sector_watch = [w for w in brief.get("watch_next") or [] if w.get("type") == "sector_exposure"]
            self.assertEqual(len(sector_watch), 0)

        for card in brief.get("cards") or []:
            self.assertIn("chip", card)
            self.assertIn("direction", card)

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
            with patch("backend.morning_brief._daily_returns_for_symbols", return_value={"NVDA": -2.0}):
                brief = build_morning_brief("u1")

        self.assertTrue(brief["cards"])
        mem = brief["cards"][0].get("memory_context") or ""
        self.assertIn("since adding", mem.lower())


if __name__ == "__main__":
    unittest.main()
