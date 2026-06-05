"""Sprint 2 — continuity moments, track record, extended morning brief."""
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.portfolio_continuity import find_continuity_moments
from backend.portfolio_track_record import build_track_record


class Sprint2Base(unittest.TestCase):
    def setUp(self):
        from backend import paper_portfolio as pp
        from backend import portfolio_memory as pm
        from backend import portfolio_snapshots_job as job

        self.pp = pp
        self.pm = pm
        self.job = job
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "progress.db")
        for mod in (pp, pm, job):
            mod.DB_PATH = self.db_path
            if hasattr(mod._local, "conn"):
                mod._local.conn.close()
                delattr(mod._local, "conn")
        pp.init_portfolio_db()

    def tearDown(self):
        for mod in (self.pp, self.pm, self.job):
            if hasattr(mod._local, "conn"):
                mod._local.conn.close()
                delattr(mod._local, "conn")
        self.tmp.cleanup()


class TestContinuityMoments(Sprint2Base):
    def test_symbol_move_rhyme_from_reaction_memory(self):
        self.pm.upsert_portfolio_reaction(
            "u1", "NVDA", "2026-06-05", move_pct=-3.0, one_line_reason="Today",
        )
        self.pm.upsert_portfolio_reaction(
            "u1", "NVDA", "2026-05-20", move_pct=-2.5, portfolio_impact_pct=-0.8,
            one_line_reason="Prior drop",
        )
        movers = [{"symbol": "NVDA", "daily_return_pct": -3.2}]
        moments = find_continuity_moments("u1", symbols=["NVDA"], top_movers=movers)
        types = {m["type"] for m in moments}
        self.assertIn("symbol_move_rhyme", types)
        rhyme = next(m for m in moments if m["type"] == "symbol_move_rhyme")
        self.assertIn("NVDA", rhyme["title"])
        self.assertIn("2026-05-20", rhyme["body"])

    def test_holding_tenure_moment(self):
        self.pm.log_portfolio_event(
            "u1", "position_added", symbol="AAPL", event_date="2026-01-10",
            title="You added AAPL",
        )
        moments = find_continuity_moments("u1", symbols=["AAPL"], top_movers=[])
        self.assertTrue(any(m["type"] == "holding_tenure" for m in moments))

    def test_portfolio_recovery_rhyme(self):
        for iso, dr in (
            ("2026-06-05", -1.2),
            ("2026-06-04", 0.8),
            ("2026-06-03", -2.0),
        ):
            self.job.upsert_snapshot({
                "user_id": "u1",
                "snapshot_date": iso,
                "portfolio_value": 1000.0,
                "recorded_at": time.time(),
                "daily_return_pct": dr,
                "positions_json": [],
                "sector_exposures": {},
            })
        moments = find_continuity_moments(
            "u1", symbols=["NVDA"], today_daily_return_pct=-1.2, top_movers=[],
        )
        self.assertTrue(any(m["type"] == "portfolio_recovery_rhyme" for m in moments))


class TestTrackRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.decisions_path = os.path.join(self.tmp.name, "decisions.db")
        os.environ["DECISIONS_DB_PATH"] = self.decisions_path
        sql = Path(__file__).resolve().parents[1] / "migrations" / "decisions" / "001_initial.sql"
        conn = sqlite3.connect(self.decisions_path)
        conn.executescript(sql.read_text())
        now = time.time()
        conn.execute(
            """
            INSERT INTO decision_events
            (decision_id, created_at, user_id, decision_type, symbol, horizon_hint, verdict)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("d1", now, "u1", "morning_brief", "", "1d", "Portfolio up 1.2%"),
        )
        conn.execute(
            """
            INSERT INTO outcome_observations
            (decision_id, horizon, as_of_ts, metric, value, correct_bool, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("d1", "1d", now, "price_return_pct", 1.2, 1, now),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("DECISIONS_DB_PATH", None)
        self.tmp.cleanup()

    def test_graded_headline(self):
        rec = build_track_record("u1", ["NVDA"])
        self.assertEqual(rec["graded_count"], 1)
        self.assertEqual(rec["directionally_right"], 1)
        self.assertIn("1 of 1", rec["headline"])

    def test_empty_user_returns_building_copy(self):
        rec = build_track_record("nobody", ["NVDA"])
        self.assertEqual(rec["graded_count"], 0)
        self.assertIn("building", rec["headline"].lower())


class TestMorningBriefSprint2(Sprint2Base):
    def test_brief_includes_session_and_continuity(self):
        from backend.morning_brief import build_morning_brief, _market_session_context

        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            with patch.object(self.pp, "_fetch_last_prices_batch", return_value={"NVDA": 100.0}):
                self.pp.add_position("u1", "NVDA", "LONG", price=80, shares=10)

        self.pm.log_portfolio_event("u1", "position_added", symbol="NVDA", event_date="2026-01-01")

        with patch("backend.morning_brief._movement_rows_for_symbols", return_value={
            "NVDA": {"daily_return_pct": -2.5, "one_line_reason": "Chip weakness"},
        }):
            brief = build_morning_brief("u1")

        self.assertIn("market_session", brief)
        self.assertIn("status", brief["market_session"])
        self.assertIn("continuity_moments", brief)
        self.assertIsInstance(brief["continuity_moments"], list)

        session = _market_session_context()
        self.assertIn(session["status"], ("open", "after_hours", "weekend"))


if __name__ == "__main__":
    unittest.main()
