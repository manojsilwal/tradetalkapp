"""
PR 6 — tests for the SEPL auto-rollback kill-switch.

Simulates a sequence:
  1. Seed registry.
  2. SEPL-commit a new version of a learnable prompt (bull).
  3. Inject post-commit reflections with low effectiveness.
  4. Run the kill switch — it should recommend / perform rollback.

All tests drive a deterministic clock to avoid time-based flakiness.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
# Deterministic tunables
os.environ["SEPL_ROLLBACK_MARGIN"] = "0.10"
os.environ["SEPL_ROLLBACK_MIN_SAMPLES"] = "3"
os.environ["SEPL_ROLLBACK_WINDOW_HOURS"] = "168"

from backend import resource_registry as rr                    # noqa: E402
from backend.resource_seeder import seed_resources_if_empty    # noqa: E402
from backend.sepl import (                                     # noqa: E402
    RollbackOutcome,
    SEPLKillSwitch,
)


def _row(ticker: str, prompt_versions: Dict[str, str], eff: float, *, date: str = "2026-04-17") -> Dict[str, Any]:
    return {
        "doc": f"Reflection for {ticker} eff={eff}",
        "meta": {
            "ticker": ticker,
            "effectiveness_score": eff,
            "prompt_versions": json.dumps(prompt_versions),
            "date": date,
        },
    }


class FakeReflections:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def fetch_recent_reflections(self, limit=200, *, only_with_prompt_versions=True):
        return list(self.rows[: max(1, int(limit))])


class _KillSwitchTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["RESOURCES_DB_PATH"] = os.path.join(self._tmp.name, "r.db")
        rr._reset_singleton_for_tests()
        self.reg = rr.get_resource_registry()
        seed_resources_if_empty(self.reg)
        self._fake_now = [time.time()]  # mutable wrapper so tests can advance

    def tearDown(self):
        rr._reset_singleton_for_tests()
        os.environ.pop("RESOURCES_DB_PATH", None)

    def _now(self):
        return self._fake_now[0]

    def _simulate_sepl_commit(self, name: str, *, new_body_suffix: str = " Revised.") -> tuple[str, str]:
        """Commit a new version of ``name`` under a ``sepl:*`` actor. Returns (prev_version, new_version)."""
        rec = self.reg.get(name)
        prev = rec.version
        updated = self.reg.update(
            name,
            rec.body + new_body_suffix,
            bump="patch",
            reason="test: simulated SEPL commit",
            actor="sepl:testrun",
        )
        return prev, updated.version


# ── Decision logic ──────────────────────────────────────────────────────────


class TestCheckNoRecentCommit(_KillSwitchTestBase):
    def test_returns_no_commit_outcome(self):
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections([]),
            now_fn=self._now,
        )
        report = ks.check("bull", dry_run=True)
        self.assertEqual(report.outcome, RollbackOutcome.NO_RECENT_SEPL_COMMIT)
        self.assertIsNone(report.restored_to_version)


class TestCheckInsufficientData(_KillSwitchTestBase):
    def test_returns_insufficient_when_below_threshold(self):
        prev, new = self._simulate_sepl_commit("bull")
        # Only 2 post-commit samples; threshold (SEPL_ROLLBACK_MIN_SAMPLES) is 3
        rows = [
            _row("A", {"bull": new}, 0.2),
            _row("B", {"bull": new}, 0.2),
            _row("C", {"bull": prev}, 0.7),
        ]
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections(rows),
            now_fn=self._now,
        )
        report = ks.check("bull", dry_run=True)
        self.assertEqual(report.outcome, RollbackOutcome.INSUFFICIENT_POST_COMMIT_DATA)
        self.assertEqual(report.post_commit_samples, 2)


class TestCheckOkWithinTolerance(_KillSwitchTestBase):
    def test_no_rollback_when_delta_within_margin(self):
        prev, new = self._simulate_sepl_commit("bull")
        # Delta = 0.0 — well within 0.10 margin
        rows = [
            _row(f"P{i}", {"bull": new}, 0.5) for i in range(5)
        ] + [
            _row(f"Q{i}", {"bull": prev}, 0.5) for i in range(5)
        ]
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections(rows),
            now_fn=self._now,
        )
        report = ks.check("bull", dry_run=True)
        self.assertEqual(report.outcome, RollbackOutcome.OK_WITHIN_TOLERANCE)
        self.assertIsNotNone(report.delta)
        self.assertLess(abs(report.delta), 0.01)

    def test_no_rollback_when_new_version_is_better(self):
        prev, new = self._simulate_sepl_commit("bull")
        rows = [
            _row(f"P{i}", {"bull": new}, 0.8) for i in range(5)
        ] + [
            _row(f"Q{i}", {"bull": prev}, 0.4) for i in range(5)
        ]
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections(rows),
            now_fn=self._now,
        )
        report = ks.check("bull", dry_run=True)
        self.assertEqual(report.outcome, RollbackOutcome.OK_WITHIN_TOLERANCE)
        self.assertGreater(report.delta, 0)


class TestCheckRegressionDryRun(_KillSwitchTestBase):
    def test_dry_run_reports_regression_without_restoring(self):
        prev, new = self._simulate_sepl_commit("bull")
        rows = [
            _row(f"P{i}", {"bull": new}, 0.2) for i in range(5)       # post: terrible
        ] + [
            _row(f"Q{i}", {"bull": prev}, 0.7) for i in range(5)      # pre: great
        ]
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections(rows),
            now_fn=self._now,
        )
        before = self.reg.active_version("bull")
        report = ks.check("bull", dry_run=True)
        self.assertEqual(report.outcome, RollbackOutcome.DRY_RUN)
        self.assertLess(report.delta, -0.10)
        self.assertEqual(self.reg.active_version("bull"), before)  # unchanged


class TestCheckRegressionLive(_KillSwitchTestBase):
    def test_live_mode_restores_prior_version(self):
        prev, new = self._simulate_sepl_commit("bull")
        rows = [
            _row(f"P{i}", {"bull": new}, 0.1) for i in range(5)
        ] + [
            _row(f"Q{i}", {"bull": prev}, 0.7) for i in range(5)
        ]
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections(rows),
            now_fn=self._now,
        )
        report = ks.check("bull", dry_run=False)
        self.assertEqual(report.outcome, RollbackOutcome.ROLLED_BACK)
        self.assertEqual(report.restored_to_version, prev)
        self.assertEqual(self.reg.active_version("bull"), prev)

        # Lineage must show the rollback with sepl:rollback: actor.
        events = self.reg.lineage("bull")
        rollback_entries = [
            e for e in events if str(e.get("actor", "")).startswith("sepl:rollback:")
        ]
        self.assertEqual(len(rollback_entries), 1)
        self.assertEqual(rollback_entries[0]["operation"], "restore")

    def test_live_mode_uses_zero_baseline_when_no_pre_rows(self):
        """If there are no pre-commit stamped reflections, baseline = 0.5.

        Post-commit eff 0.2 < 0.5 - 0.10, so rollback should fire.
        """
        _prev, new = self._simulate_sepl_commit("bull")
        rows = [_row(f"P{i}", {"bull": new}, 0.2) for i in range(5)]
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections(rows),
            now_fn=self._now,
        )
        report = ks.check("bull", dry_run=True)
        self.assertEqual(report.outcome, RollbackOutcome.DRY_RUN)
        self.assertIsNotNone(report.delta)
        self.assertLess(report.delta, -0.10)


class TestCheckAll(_KillSwitchTestBase):
    def test_check_all_visits_all_learnable(self):
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections([]),
            now_fn=self._now,
        )
        reports = ks.check_all(dry_run=True)
        # Should produce one report per learnable prompt.
        # In Phase A, bull, bear, macro, value, contrarian, and swarm_analyst
        # are all learnable — so at least 5.
        self.assertGreaterEqual(len(reports), 5)
        # None of them have SEPL commits → every outcome should be NO_RECENT_SEPL_COMMIT.
        for r in reports:
            self.assertEqual(r.outcome, RollbackOutcome.NO_RECENT_SEPL_COMMIT)

    def test_check_all_skips_pinned(self):
        """Pinned prompts (moderator, swarm_synthesizer, etc.) must not appear."""
        ks = SEPLKillSwitch(
            registry=self.reg,
            reflection_source=FakeReflections([]),
            now_fn=self._now,
        )
        reports = ks.check_all(dry_run=True)
        names = {r.target_name for r in reports}
        self.assertNotIn("moderator", names)
        self.assertNotIn("swarm_synthesizer", names)
        self.assertNotIn("sepl_improver", names)


class TestErrorRecovery(_KillSwitchTestBase):
    def test_restore_failure_surfaces_as_error(self):
        prev, new = self._simulate_sepl_commit("bull")
        rows = [_row(f"P{i}", {"bull": new}, 0.1) for i in range(5)]

        class BreakingRegistry:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, n):
                return getattr(self._real, n)

            def restore(self, *a, **k):
                raise RuntimeError("simulated restore failure")

        broken = BreakingRegistry(self.reg)
        ks = SEPLKillSwitch(
            registry=broken,
            reflection_source=FakeReflections(rows),
            now_fn=self._now,
        )
        report = ks.check("bull", dry_run=False)
        self.assertEqual(report.outcome, RollbackOutcome.ERROR)
        # Registry state must be unchanged (we never succeeded).
        self.assertEqual(self.reg.active_version("bull"), new)


if __name__ == "__main__":
    unittest.main()
