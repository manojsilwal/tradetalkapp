"""
Phase C2 PR 1 — integration tests for the tier-1 tool
``macro_vix_to_credit_stress``.

Covers:
    * Flag-OFF regression — ``MacroHealthConnector`` produces byte-identical
      output (credit_stress_index, status) to the pre-evolution code path
      (``round(vix / 15.0, 2)``, threshold 1.1) when the registry is disabled.
    * Flag-ON override — with a SEPL-written config in the registry, the same
      VIX level yields the new CSI and status.
    * Tier-aware budget gate — tier-1 commits are capped at 1/day by default;
      tier-0 commits at 2/day. Both are overridable via env.

These tests never hit the network: the yfinance callables are monkey-patched
to return a fixed VIX level and empty sector/flow lists.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.resource_registry import (  # noqa: E402
    ResourceKind,
    ResourceRecord,
    ResourceRegistry,
    _reset_singleton_for_tests,
)
from backend.resource_seeder import seed_resources_if_empty  # noqa: E402
from backend.sepl_tool import (  # noqa: E402
    SEPLTool,
    ToolSEPLOutcome,
    tool_sepl_max_per_day_for_tier,
)
from backend.tool_configs import update_tool_config  # noqa: E402


class _EnvIsolated(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "r.db")
        self._env_keys = (
            "RESOURCES_DB_PATH",
            "RESOURCES_USE_REGISTRY",
            "SEPL_TOOL_ENABLE", "SEPL_TOOL_DRY_RUN",
            "SEPL_TOOL_MIN_MARGIN", "SEPL_TOOL_MAX_PER_DAY",
            "SEPL_TOOL_MAX_PER_DAY_TIER_0",
            "SEPL_TOOL_MAX_PER_DAY_TIER_1",
            "SEPL_TOOL_MAX_PER_DAY_TIER_2",
            "SEPL_TOOL_CANDIDATES_PER_CYCLE",
        )
        self._orig_env = {k: os.environ.get(k) for k in self._env_keys}
        _reset_singleton_for_tests()

    def tearDown(self) -> None:
        for k in self._env_keys:
            v = self._orig_env.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_singleton_for_tests()


def _mock_yfinance_with_vix(vix_level: float):
    """Patch yfinance so ``MacroHealthConnector`` returns ``vix_level``."""

    class _FakeHist:
        def __init__(self, v):
            self._v = v
            self.empty = False

        def __getitem__(self, k):
            assert k == "Close"
            class _S:
                def __init__(self, v): self._v = v
                @property
                def iloc(self): return [self._v]
            return _S(self._v)

    class _FakeVix:
        def __init__(self, v): self._v = v
        def history(self, period="1d"):
            return _FakeHist(self._v)

    def _fake_ticker(symbol):
        return _FakeVix(vix_level)

    class _FakeTickers:
        def __init__(self, *a, **kw):
            self.tickers = {}
        def __getattr__(self, _name):
            return {}

    return mock.patch("backend.connectors.macro.yf", mock.MagicMock(
        Ticker=_fake_ticker,
        Tickers=_FakeTickers,
    ))


# ── Flag-off regression ───────────────────────────────────────────────────────


class TestMacroConnectorFlagOff(_EnvIsolated):
    def _run(self, vix: float):
        from backend.connectors.macro import MacroHealthConnector
        conn = MacroHealthConnector()
        with _mock_yfinance_with_vix(vix):
            return asyncio.run(conn.fetch_data())

    def test_legacy_formula_at_median_regime(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        out = self._run(vix=15.0)
        self.assertAlmostEqual(out["indicators"]["credit_stress_index"], 1.0)
        self.assertEqual(out["status"], "Normal")

    def test_legacy_formula_in_stress_regime(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        out = self._run(vix=30.0)
        self.assertAlmostEqual(out["indicators"]["credit_stress_index"], 2.0)
        self.assertEqual(out["status"], "Stress Detected")

    def test_status_boundary_is_strictly_greater_than(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        # VIX=16.5 → csi=1.1 → NOT > 1.1 → "Normal"
        out = self._run(vix=16.5)
        self.assertAlmostEqual(out["indicators"]["credit_stress_index"], 1.1)
        self.assertEqual(out["status"], "Normal")


# ── Flag-on registry override ────────────────────────────────────────────────


class TestMacroConnectorRegistryOverride(_EnvIsolated):
    def setUp(self) -> None:
        super().setUp()
        os.environ["RESOURCES_DB_PATH"] = self.db_path
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        _reset_singleton_for_tests()
        seed_resources_if_empty()

    def test_override_divisor_changes_csi(self):
        update_tool_config(
            "macro_vix_to_credit_stress",
            {"divisor": 10.0, "status_threshold": 1.1},
            reason="test override",
            actor="test",
        )
        from backend.connectors.macro import MacroHealthConnector
        conn = MacroHealthConnector()
        with _mock_yfinance_with_vix(15.0):
            out = asyncio.run(conn.fetch_data())
        # 15 / 10 = 1.5  → above 1.1 threshold → STRESS
        self.assertAlmostEqual(out["indicators"]["credit_stress_index"], 1.5)
        self.assertEqual(out["status"], "Stress Detected")

    def test_override_threshold_changes_status_without_csi_change(self):
        update_tool_config(
            "macro_vix_to_credit_stress",
            {"divisor": 15.0, "status_threshold": 0.5},
            reason="tighter threshold",
            actor="test",
        )
        from backend.connectors.macro import MacroHealthConnector
        conn = MacroHealthConnector()
        with _mock_yfinance_with_vix(10.0):
            out = asyncio.run(conn.fetch_data())
        # 10/15 = 0.67 > 0.5 → STRESS, csi unchanged from legacy.
        self.assertAlmostEqual(out["indicators"]["credit_stress_index"], 0.67)
        self.assertEqual(out["status"], "Stress Detected")


# ── Tier-aware budget gate ───────────────────────────────────────────────────


class TestTierBudgetGate(_EnvIsolated):
    def test_tier0_default_is_two_per_day(self):
        os.environ.pop("SEPL_TOOL_MAX_PER_DAY_TIER_0", None)
        self.assertEqual(tool_sepl_max_per_day_for_tier(0), 2)

    def test_tier1_default_is_one_per_day(self):
        os.environ.pop("SEPL_TOOL_MAX_PER_DAY_TIER_1", None)
        self.assertEqual(tool_sepl_max_per_day_for_tier(1), 1)

    def test_tier2_default_blocks_commits(self):
        os.environ.pop("SEPL_TOOL_MAX_PER_DAY_TIER_2", None)
        self.assertEqual(tool_sepl_max_per_day_for_tier(2), 0)

    def test_tier3_default_blocks_commits(self):
        os.environ.pop("SEPL_TOOL_MAX_PER_DAY_TIER_3", None)
        self.assertEqual(tool_sepl_max_per_day_for_tier(3), 0)

    def test_unknown_tier_blocks_commits(self):
        self.assertEqual(tool_sepl_max_per_day_for_tier(99), 0)

    def test_env_override_respected(self):
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_1"] = "5"
        self.assertEqual(tool_sepl_max_per_day_for_tier(1), 5)

    def test_env_override_negative_clamped_to_zero(self):
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_1"] = "-3"
        self.assertEqual(tool_sepl_max_per_day_for_tier(1), 0)

    def test_env_override_garbage_falls_back(self):
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_1"] = "not-an-int"
        self.assertEqual(tool_sepl_max_per_day_for_tier(1), 1)  # default


class TestTierGateEnforcedInCommit(_EnvIsolated):
    """End-to-end: SEPL.commit must refuse a second Tier-1 commit within 24h
    even when the global ``SEPL_TOOL_MAX_PER_DAY`` is larger."""

    def setUp(self) -> None:
        super().setUp()
        os.environ["RESOURCES_DB_PATH"] = self.db_path
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        _reset_singleton_for_tests()
        # Seed a tier-1 tool.
        self.reg = ResourceRegistry(db_path=self.db_path)
        rec = ResourceRecord(
            name="macro_t1", kind=ResourceKind.TOOL, version="1.0.0",
            description="tier-1 test tool", learnable=True, body="",
            metadata={
                "tier": 1,
                "config": {"threshold": 5.0},
                "parameter_ranges": {"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
            },
            schema={"type": "object", "required": ["threshold"],
                    "properties": {"threshold": {"type": "number", "minimum": 1.0, "maximum": 10.0}}},
            fallback={"threshold": 5.0},
        )
        self.reg.register(rec)
        self.sepl = SEPLTool(registry=self.reg)

    def test_tier1_commits_capped_at_one_per_day(self):
        from backend.sepl_tool import ToolEvalResult
        fake = ToolEvalResult(
            tool_name="macro_t1", active_version="1.0.0",
            active_score=0.0, candidate_score=1.0, margin=1.0,
            fixtures_used=1, active_hits=0, candidate_hits=1,
        )
        os.environ["SEPL_TOOL_MAX_PER_DAY"] = "10"        # global very permissive
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_1"] = "1"  # tier-1 capped at 1

        o1, v1 = self.sepl.commit("macro_t1", fake, {"threshold": 6.0},
                                  dry_run=False, run_id="r1")
        self.assertEqual(o1, ToolSEPLOutcome.COMMITTED)
        self.assertEqual(v1, "1.0.1")

        o2, v2 = self.sepl.commit("macro_t1", fake, {"threshold": 6.5},
                                  dry_run=False, run_id="r2")
        self.assertEqual(o2, ToolSEPLOutcome.REJECTED_RATE_LIMIT)
        self.assertIsNone(v2)

    def test_tier_cap_of_zero_blocks_all_commits(self):
        from backend.sepl_tool import ToolEvalResult
        fake = ToolEvalResult(
            tool_name="macro_t1", active_version="1.0.0",
            active_score=0.0, candidate_score=1.0, margin=1.0,
            fixtures_used=1, active_hits=0, candidate_hits=1,
        )
        os.environ["SEPL_TOOL_MAX_PER_DAY"] = "10"
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_1"] = "0"
        o, v = self.sepl.commit("macro_t1", fake, {"threshold": 6.0},
                                dry_run=False, run_id="r1")
        self.assertEqual(o, ToolSEPLOutcome.REJECTED_RATE_LIMIT)
        self.assertIsNone(v)


if __name__ == "__main__":
    unittest.main()
