"""Phase 5–6 — user-actions log + portfolio timeline (module-level tests)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from backend import portfolio_timeline as pt
from backend.portfolio_timeline import build_timeline


class PortfolioRoutesPhase56Base(unittest.TestCase):
    def setUp(self):
        from backend import paper_portfolio as pp
        from backend import portfolio_memory as pm

        self.pp = pp
        self.pm = pm
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "progress.db")
        pp.DB_PATH = self.db_path
        pm.DB_PATH = self.db_path
        pt.DB_PATH = self.db_path
        for mod in (pp, pm, pt):
            if hasattr(mod._local, "conn"):
                mod._local.conn.close()
                delattr(mod._local, "conn")
        pp.init_portfolio_db()

    def tearDown(self):
        for mod in (self.pp, self.pm, pt):
            if hasattr(mod._local, "conn"):
                mod._local.conn.close()
                delattr(mod._local, "conn")
        self.tmp.cleanup()


class TestUserActionsPhase5(PortfolioRoutesPhase56Base):
    def test_log_user_action_persists(self):
        aid = self.pm.log_user_action(
            "u1",
            "brief_card_click",
            entity_type="morning_brief_card",
            entity_id="card_1",
            symbol="NVDA",
            page="your_morning",
            metadata={"card_type": "top_negative_contributor"},
        )
        self.assertTrue(aid)
        rows = self.pm.list_user_actions("u1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action_type"], "brief_card_click")
        self.assertEqual(rows[0]["symbol"], "NVDA")

    def test_learn_from_action_dual_write(self):
        from backend import user_preferences as uprefs

        uprefs.DB_PATH = self.db_path
        if hasattr(uprefs._local, "pref_conn"):
            uprefs._local.pref_conn.close()
            delattr(uprefs._local, "pref_conn")
        uprefs.init_preferences_db()

        self.pm.log_user_action("u1", "ticker_click", symbol="AAPL", page="your_morning")
        try:
            from backend import user_preferences as uprefs2

            uprefs2.learn_from_action("u1", "ticker_click", {"ticker": "AAPL"})
            signals = uprefs2.get_signals("u1")
            self.assertGreaterEqual(signals.get("ticker_counts", {}).get("AAPL", 0), 1)
        finally:
            pass


class TestTimelinePhase6(PortfolioRoutesPhase56Base):
    def test_timeline_merges_events_and_reactions(self):
        self.pm.log_portfolio_event("u1", "position_added", symbol="NVDA", title="You added NVDA")
        self.pm.upsert_portfolio_reaction(
            "u1", "NVDA", "2026-06-05", move_pct=-3.0, one_line_reason="Guidance concerns",
        )
        items = build_timeline("u1", limit=10)
        kinds = {i["kind"] for i in items}
        self.assertIn("portfolio_event", kinds)
        self.assertIn("reaction_memory", kinds)

    def test_timeline_user_scoped(self):
        self.pm.log_portfolio_event("u_a", "position_added", symbol="NVDA")
        self.pm.log_portfolio_event("u_b", "position_added", symbol="MSFT")
        a_items = build_timeline("u_a")
        self.assertTrue(all(i.get("symbol") in (None, "NVDA") for i in a_items if i["kind"] == "portfolio_event"))
        self.assertEqual(a_items[0]["symbol"], "NVDA")

    def test_timeline_after_position_add_hook(self):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            self.pp.add_position("u1", "NVDA", "LONG", price=100, shares=1)
        items = build_timeline("u1")
        self.assertGreaterEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
