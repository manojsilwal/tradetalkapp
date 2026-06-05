"""Phase 2 — nightly portfolio snapshot job (Your Morning v0)."""
import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd


class PortfolioSnapshotsPhase2Base(unittest.TestCase):
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
            if mod is pp:
                mod.DB_PATH = self.db_path
            elif mod is pm:
                mod.DB_PATH = self.db_path
            elif mod is job:
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
        self.pp.DB_PATH = os.path.join(
            os.path.dirname(self.pp.__file__), "..", "progress.db"
        )
        self.pm.DB_PATH = self.pp.DB_PATH
        self.job.DB_PATH = self.pp.DB_PATH
        self.tmp.cleanup()

    def _add_nvda(self, user_id: str = "u1"):
        profile = {
            "sector": "Technology",
            "market_cap": 1e12,
            "cap_bucket": "Mega Cap",
            "asset_type": "Equity",
        }
        with patch("backend.paper_portfolio._fetch_ticker_profile", return_value=profile):
            with patch.object(self.pp, "_fetch_last_prices_batch", return_value={"NVDA": 100.0}):
                return self.pp.add_position(user_id, "NVDA", "LONG", price=80, shares=10)


class TestSnapshotCalculation(PortfolioSnapshotsPhase2Base):
    def test_calculate_snapshot_shape(self):
        self._add_nvda()
        snap_date = date(2026, 6, 5)

        def fake_hist(ticker, trade_date, fallback):
            return -2.0 if ticker == "NVDA" else 0.0

        with patch.object(self.job, "_position_daily_return_pct", side_effect=fake_hist):
            with patch.object(self.job, "_benchmark_daily_return_pct", return_value=-0.3):
                with patch.object(self.job, "_benchmark_close_on_date", return_value=500.0):
                    payload = self.job.calculate_snapshot_for_user("u1", snap_date)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["user_id"], "u1")
        self.assertEqual(payload["snapshot_date"], "2026-06-05")
        self.assertGreater(payload["portfolio_value"], 0)
        positions = payload["positions_json"]
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "NVDA")
        self.assertIn("portfolio_weight", positions[0])

    def test_daily_return_uses_previous_snapshot(self):
        self._add_nvda()
        snap_date = date(2026, 6, 5)
        prev = {
            "user_id": "u1",
            "snapshot_date": "2026-06-04",
            "portfolio_value": 900.0,
            "positions_json": "[]",
            "sector_exposures": "{}",
        }
        with patch.object(self.job, "_get_snapshot_for_date", return_value=prev):
            with patch.object(self.job, "_position_daily_return_pct", return_value=0.0):
                with patch.object(self.job, "_benchmark_daily_return_pct", return_value=0.0):
                    with patch.object(self.job, "_benchmark_close_on_date", return_value=500.0):
                        with patch.object(
                            self.pp,
                            "get_portfolio_performance",
                            return_value={
                                "positions": [{
                                    "ticker": "NVDA",
                                    "entry_price": 80,
                                    "entry_date": "2026-03-12",
                                    "shares": 10,
                                    "allocated": 800,
                                    "current_price": 100,
                                    "current_value": 1000,
                                    "sector": "Technology",
                                }],
                                "total_value": 1000.0,
                                "analysis": {"by_sector": {"Technology": 1000.0}},
                            },
                        ):
                            payload = self.job.calculate_snapshot_for_user("u1", snap_date)

        self.assertAlmostEqual(payload["daily_return_pct"], 11.111, places=2)
        self.assertAlmostEqual(payload["daily_return_value"], 100.0, places=2)


class TestSnapshotPersistence(PortfolioSnapshotsPhase2Base):
    def test_upsert_idempotent(self):
        payload = {
            "user_id": "u1",
            "snapshot_date": "2026-06-05",
            "portfolio_value": 1000.0,
            "spy_value": 500.0,
            "positions_json": [{"symbol": "NVDA"}],
            "recorded_at": 1.0,
            "daily_return_pct": -0.8,
            "daily_return_value": -8.0,
            "cumulative_return_pct": 14.0,
            "qqq_return_pct": -0.6,
            "spy_return_pct": -0.3,
            "top_position_symbol": "NVDA",
            "top_position_weight": 1.0,
            "sector_exposures": {"Technology": 1.0},
        }
        self.assertTrue(self.job.upsert_snapshot(payload))
        payload["portfolio_value"] = 1050.0
        payload["daily_return_pct"] = 5.0
        self.assertTrue(self.job.upsert_snapshot(payload))

        conn = self.pm._get_conn()
        row = conn.execute(
            "SELECT portfolio_value, daily_return_pct FROM portfolio_snapshots WHERE user_id=?",
            ("u1",),
        ).fetchone()
        self.assertEqual(row["portfolio_value"], 1050.0)
        self.assertEqual(row["daily_return_pct"], 5.0)

    def test_write_job_processes_users(self):
        self._add_nvda("user_a")
        self._add_nvda("user_b")

        minimal_payload = {
            "user_id": "user_a",
            "snapshot_date": date.today().isoformat(),
            "portfolio_value": 500.0,
            "spy_value": 1.0,
            "positions_json": [],
            "recorded_at": 1.0,
            "sector_exposures": {},
        }

        with patch.object(
            self.job,
            "calculate_snapshot_for_user",
            side_effect=lambda uid, d=None: {**minimal_payload, "user_id": uid},
        ):
            with patch.object(self.job, "detect_events_from_snapshot", return_value=[]):
                summary = self.job.write_portfolio_snapshots()

        self.assertEqual(summary["users_processed"], 2)
        self.assertEqual(summary["snapshots_written"], 2)


class TestEventDetection(PortfolioSnapshotsPhase2Base):
    def test_big_move_logs_reaction_memory(self):
        payload = {
            "user_id": "u1",
            "snapshot_date": "2026-06-05",
            "portfolio_value": 1000.0,
            "positions_json": [{
                "symbol": "NVDA",
                "portfolio_weight": 0.5,
                "daily_return_pct": -4.0,
                "cumulative_return_since_entry_pct": 5.0,
            }],
            "top_position_symbol": "NVDA",
            "sector_exposures": {"Technology": 0.5},
        }
        events = self.job.detect_events_from_snapshot(payload, None)
        self.assertIn("big_move_for_held_position", events)
        conn = self.pm._get_conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM portfolio_reaction_memory WHERE user_id=?",
            ("u1",),
        ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_gain_milestone_crossing(self):
        payload = {
            "user_id": "u1",
            "snapshot_date": "2026-06-05",
            "portfolio_value": 1000.0,
            "positions_json": [{
                "symbol": "NVDA",
                "portfolio_weight": 1.0,
                "daily_return_pct": 1.0,
                "cumulative_return_since_entry_pct": 12.0,
            }],
            "top_position_symbol": "NVDA",
            "sector_exposures": {},
        }
        previous = {
            "top_position_symbol": "NVDA",
            "positions_json": json.dumps([{
                "symbol": "NVDA",
                "cumulative_return_since_entry_pct": 8.0,
            }]),
            "sector_exposures": "{}",
        }
        events = self.job.detect_events_from_snapshot(payload, previous)
        self.assertIn("position_crossed_gain_threshold", events)


if __name__ == "__main__":
    unittest.main()
