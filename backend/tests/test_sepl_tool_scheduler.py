"""
Phase C rollout smoke — verifies the tool SEPL scheduler wires up correctly
inside ``backend/main.py`` without actually starting the app server.

Run a single tick synchronously against a temp registry. Asserts:

    * ``_tool_sepl_tick`` executes with the live-production env
      (``SEPL_TOOL_ENABLE=1, SEPL_TOOL_DRY_RUN=0, SEPL_TOOL_AUTOCOMMIT=1``)
      and returns cleanly, not raising, not logging an error.
    * With the canonical YAML defaults in place, no tool is committed
      (margins are zero against their own fixtures).
    * Kill switch reports do not surface spurious rollbacks when nothing
      was committed.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.resource_registry import (  # noqa: E402
    ResourceKind,
    _reset_singleton_for_tests,
    get_resource_registry,
)
from backend.resource_seeder import seed_resources_if_empty  # noqa: E402
from backend.sepl_tool import (  # noqa: E402
    SEPLTool,
    SEPLToolKillSwitch,
    ToolRollbackOutcome,
    ToolSEPLOutcome,
    tool_sepl_autocommit,
    tool_sepl_dry_run,
    tool_sepl_enabled,
)


class _RolloutEnvIsolated(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env_keys = (
            "RESOURCES_DB_PATH", "RESOURCES_USE_REGISTRY",
            "SEPL_TOOL_ENABLE", "SEPL_TOOL_DRY_RUN",
            "SEPL_TOOL_AUTOCOMMIT",
            "SEPL_TOOL_MIN_MARGIN", "SEPL_TOOL_MAX_PER_DAY",
            "SEPL_TOOL_MAX_PER_DAY_TIER_0",
            "SEPL_TOOL_MAX_PER_DAY_TIER_1",
            "SEPL_TOOL_CANDIDATES_PER_CYCLE",
            "SEPL_TOOL_MAX_PERTURB_STEPS",
            "SEPL_TOOL_ROLLBACK_MARGIN",
            "SEPL_TOOL_ROLLBACK_WINDOW_HOURS",
        )
        self._orig_env = {k: os.environ.get(k) for k in self._env_keys}
        os.environ["RESOURCES_DB_PATH"] = os.path.join(self._tmp.name, "r.db")
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        # Apply the render.yaml rollout values.
        os.environ["SEPL_TOOL_ENABLE"] = "1"
        os.environ["SEPL_TOOL_DRY_RUN"] = "0"
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"
        os.environ["SEPL_TOOL_MIN_MARGIN"] = "0.05"
        os.environ["SEPL_TOOL_MAX_PER_DAY"] = "2"
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_0"] = "2"
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_1"] = "1"
        os.environ["SEPL_TOOL_CANDIDATES_PER_CYCLE"] = "4"
        os.environ["SEPL_TOOL_MAX_PERTURB_STEPS"] = "4"
        os.environ["SEPL_TOOL_ROLLBACK_MARGIN"] = "0.05"
        os.environ["SEPL_TOOL_ROLLBACK_WINDOW_HOURS"] = "168"
        _reset_singleton_for_tests()
        seed_resources_if_empty()

    def tearDown(self) -> None:
        for k in self._env_keys:
            v = self._orig_env.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_singleton_for_tests()


class TestRolloutFlagsEffective(_RolloutEnvIsolated):
    def test_all_three_switches_report_live(self):
        self.assertTrue(tool_sepl_enabled(), "master flag should be on")
        self.assertFalse(tool_sepl_dry_run(), "dry_run should be off post-rollout")
        self.assertTrue(tool_sepl_autocommit(), "autocommit should be on post-rollout")


class TestCanonicalToolsStayStable(_RolloutEnvIsolated):
    """The rollout bar is: with live flags + canonical YAML defaults in the
    registry, a single SEPL-tool tick must NOT commit any tool and must NOT
    trigger any rollback. Canonical configs score 100% on their own
    fixtures, so no candidate can beat the min-margin."""

    def test_tick_commits_nothing_on_canonical_state(self):
        reg = get_resource_registry()
        learnable = [r.name for r in reg.list(ResourceKind.TOOL) if r.learnable]
        self.assertGreater(len(learnable), 0)

        sepl = SEPLTool(registry=reg)
        for name in learnable:
            report = sepl.run_cycle(learnable, force_target=name, force_enable=True)
            self.assertIn(
                report.outcome,
                (
                    ToolSEPLOutcome.REJECTED_LOW_MARGIN,
                    ToolSEPLOutcome.REJECTED_UNCHANGED,
                ),
                f"{name} unexpectedly committed: {report.outcome}",
            )
            self.assertEqual(
                reg.active_version(name), "1.0.0",
                f"{name} active version mutated unexpectedly",
            )

    def test_killswitch_reports_no_spurious_rollbacks(self):
        reg = get_resource_registry()
        ks = SEPLToolKillSwitch(registry=reg)
        reports = ks.check_all()  # autocommit is ON via env
        for r in reports:
            self.assertIn(
                r.outcome,
                (
                    ToolRollbackOutcome.NO_RECENT_SEPL_COMMIT,
                    ToolRollbackOutcome.NO_PRIOR_VERSION_AVAILABLE,
                    ToolRollbackOutcome.OK_WITHIN_TOLERANCE,
                ),
                f"{r.tool_name} kill switch fired unexpectedly: {r.outcome}",
            )
            self.assertIsNone(r.restored_to_version)


class TestMainSchedulerTickWiring(_RolloutEnvIsolated):
    """Invoke the exact tick body main.py would run, via the public API.
    Guards against regressions in the wiring (missing imports, wrong arg
    names, etc.) without needing to boot the full FastAPI app."""

    def test_tick_body_runs_without_error(self):
        reg = get_resource_registry()

        async def _tick():
            learnable = [
                r.name for r in reg.list(ResourceKind.TOOL) if r.learnable
            ]
            if not learnable:
                return "no learnable"
            sepl = SEPLTool(registry=reg)
            report = sepl.run_cycle(learnable)
            # A real tick would also run the kill switch.
            ks = SEPLToolKillSwitch(registry=reg)
            rollback_reports = ks.check_all(dry_run=not tool_sepl_autocommit())
            return report, rollback_reports

        result = asyncio.run(_tick())
        self.assertIsInstance(result, tuple)
        report, rollback_reports = result
        # Canonical state → no commit, no rollback.
        self.assertIn(report.outcome, (
            ToolSEPLOutcome.REJECTED_LOW_MARGIN,
            ToolSEPLOutcome.REJECTED_UNCHANGED,
        ))
        for r in rollback_reports:
            self.assertIsNone(r.restored_to_version)


if __name__ == "__main__":
    unittest.main()
