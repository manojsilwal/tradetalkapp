"""Phase 1 — portfolio memory data foundation (Your Morning v0)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.migrations.runner import run_migrations


class PortfolioMemoryTestBase(unittest.TestCase):
    def setUp(self):
        from backend import paper_portfolio as pp
        from backend import portfolio_memory as pm

        self.pp = pp
        self.pm = pm
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "progress.db")
        self.old_pp_path = pp.DB_PATH
        self.old_pm_path = pm.DB_PATH
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
        self.pp.DB_PATH = self.old_pp_path
        self.pm.DB_PATH = self.old_pm_path
        self.tmp.cleanup()


class TestPortfolioMemorySchema(PortfolioMemoryTestBase):
    def test_migration_creates_memory_tables(self):
        conn = self.pm._get_conn()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("portfolio_events", tables)
        self.assertIn("user_actions", tables)
        self.assertIn("portfolio_reaction_memory", tables)

    def test_snapshot_extended_columns(self):
        cols = self.pm.snapshot_table_columns()
        for name in (
            "daily_return_pct",
            "daily_return_value",
            "cumulative_return_pct",
            "qqq_return_pct",
            "top_position_symbol",
            "top_position_weight",
            "sector_exposures",
        ):
            self.assertIn(name, cols, f"missing column {name}")

    def test_migration_idempotent(self):
        applied = run_migrations(self.db_path, "progress")
        self.assertEqual(applied, [])
        conn = self.pm._get_conn()
        n = conn.execute("SELECT COUNT(*) FROM portfolio_events").fetchone()[0]
        self.assertEqual(n, 0)


class TestPortfolioEventLogging(PortfolioMemoryTestBase):
    def test_log_and_list_events_scoped_by_user(self):
        self.pm.log_portfolio_event(
            "user_a",
            "position_added",
            symbol="NVDA",
            title="You added NVDA",
        )
        self.pm.log_portfolio_event(
            "user_b",
            "position_added",
            symbol="MSFT",
            title="You added MSFT",
        )
        a_events = self.pm.list_portfolio_events("user_a")
        b_events = self.pm.list_portfolio_events("user_b")
        self.assertEqual(len(a_events), 1)
        self.assertEqual(a_events[0]["symbol"], "NVDA")
        self.assertEqual(len(b_events), 1)
        self.assertEqual(b_events[0]["symbol"], "MSFT")

    def test_user_actions_scoped_by_user(self):
        self.pm.log_user_action("u1", "page_open", page="your_morning")
        self.pm.log_user_action("u2", "page_open", page="dashboard")
        u1 = self.pm.list_user_actions("u1")
        self.assertEqual(len(u1), 1)
        self.assertEqual(u1[0]["page"], "your_morning")
        self.assertEqual(len(self.pm.list_user_actions("u2")), 1)

    def test_reaction_memory_upsert_idempotent(self):
        d = "2026-06-05"
        self.pm.upsert_portfolio_reaction(
            "u1", "NVDA", d, move_pct=-2.1, one_line_reason="Guidance concerns",
        )
        self.pm.upsert_portfolio_reaction(
            "u1", "NVDA", d, move_pct=-3.0, one_line_reason="Updated reason",
        )
        conn = self.pm._get_conn()
        rows = conn.execute(
            "SELECT move_pct, one_line_reason FROM portfolio_reaction_memory WHERE user_id=?",
            ("u1",),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["move_pct"], -3.0)
        self.assertEqual(rows[0]["one_line_reason"], "Updated reason")


class TestPortfolioMutationHooks(PortfolioMemoryTestBase):
    def test_add_position_emits_event(self):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            result = self.pp.add_position("u1", "nvda", "LONG", price=100, shares=5)
        self.assertNotIn("error", result)
        events = self.pm.list_portfolio_events("u1")
        types = [e["event_type"] for e in events]
        self.assertIn("position_added", types)
        self.assertEqual(events[0]["symbol"], "NVDA")

    def test_close_position_emits_removed_event(self):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            added = self.pp.add_position("u1", "AAPL", "LONG", price=100, shares=2)
        self.pm.list_portfolio_events("u1")  # clear count baseline — still has add

        class _Ticker:
            @property
            def fast_info(self):
                return {"lastPrice": 110.0}

        with patch("yfinance.Ticker", return_value=_Ticker()):
            closed = self.pp.close_position("u1", added["id"])
        self.assertTrue(closed.get("closed"))
        events = self.pm.list_portfolio_events("u1")
        types = [e["event_type"] for e in events]
        self.assertIn("position_removed", types)

    def test_import_emits_portfolio_imported_and_add_events(self):
        items = [{"ticker": "MSFT", "shares": 10, "avg_cost": 400}]
        with patch(
            "backend.paper_portfolio._resolve_import_entry_price", return_value=400.0
        ):
            with patch("backend.paper_portfolio._fetch_ticker_profile", return_value={
                "sector": "Technology",
                "market_cap": 1e12,
                "cap_bucket": "Mega Cap",
                "asset_type": "Equity",
            }):
                result = self.pp.apply_holdings_import(
                    "u1", items, full_snapshot=True, source="test_import"
                )
        self.assertIn("MSFT", result["applied"])
        events = self.pm.list_portfolio_events("u1")
        types = [e["event_type"] for e in events]
        self.assertIn("portfolio_imported", types)
        self.assertIn("position_added", types)

    def test_full_snapshot_import_emits_removed_for_dropped_ticker(self):
        items_a = [{"ticker": "AAPL", "shares": 5, "avg_cost": 150}]
        items_b = [{"ticker": "MSFT", "shares": 3, "avg_cost": 400}]
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._resolve_import_entry_price", side_effect=[150.0, 400.0, 400.0]):
            with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
                self.pp.apply_holdings_import("u1", items_a, full_snapshot=True)
                self.pp.apply_holdings_import("u1", items_b, full_snapshot=True)
        events = self.pm.list_portfolio_events("u1")
        removed = [e for e in events if e["event_type"] == "position_removed"]
        symbols = {e["symbol"] for e in removed}
        self.assertIn("AAPL", symbols)


if __name__ == "__main__":
    unittest.main()
