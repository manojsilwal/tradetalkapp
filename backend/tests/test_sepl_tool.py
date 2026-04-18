"""
Tests for Phase C1 PR 2 — SEPL operators for TOOL resources.

Safety invariants verified:
    * Master flag ``SEPL_TOOL_ENABLE=0`` means ``run_cycle`` never mutates.
    * Dry-run mode stops before ``update_tool_config``.
    * Pinned tools are never updated regardless of margin.
    * Rate limit ``SEPL_TOOL_MAX_PER_DAY`` is honored.
    * Improve stays within ``parameter_ranges`` and never invents keys.
    * Evaluate never calls any I/O (mocked handler contract).
    * Commit honors ``SEPL_TOOL_MIN_MARGIN``.
"""
from __future__ import annotations

import json
import os
import random
import tempfile
import unittest
from pathlib import Path

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
    SEPLToolKillSwitch,
    ToolRollbackOutcome,
    ToolSEPLOutcome,
    _load_parameter_ranges,
    tool_sepl_autocommit,
    tool_sepl_dry_run,
    tool_sepl_enabled,
    tool_sepl_min_margin,
    tool_sepl_rollback_margin,
    tool_sepl_rollback_window_hours,
)
from backend.tool_configs import get_tool_config  # noqa: E402


def _record(
    name: str = "t0",
    *,
    version: str = "1.0.0",
    learnable: bool = True,
    config: dict | None = None,
    fallback: dict | None = None,
    ranges: dict | None = None,
) -> ResourceRecord:
    return ResourceRecord(
        name=name,
        kind=ResourceKind.TOOL,
        version=version,
        description=f"{name} desc",
        learnable=learnable,
        body="# docstring",
        schema={"type": "object"},
        fallback=fallback if fallback is not None else {"x": 0.0},
        metadata={
            "config": dict(config if config is not None else {"x": 1.0}),
            "parameter_ranges": dict(ranges if ranges is not None else {
                "x": {"min": 0.0, "max": 10.0, "step": 0.5},
            }),
            "tier": 0,
        },
    )


class _EnvIsolated(unittest.TestCase):
    """Isolate env flags + registry singleton across tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "r.db")
        self._fixture_dir = Path(self._tmp.name) / "fix"
        self._fixture_dir.mkdir()

        self._env_keys = (
            "RESOURCES_DB_PATH", "RESOURCES_USE_REGISTRY",
            "SEPL_TOOL_ENABLE", "SEPL_TOOL_DRY_RUN",
            "SEPL_TOOL_MIN_MARGIN", "SEPL_TOOL_MAX_PER_DAY",
            "SEPL_TOOL_MAX_PERTURB_STEPS", "SEPL_TOOL_CANDIDATES_PER_CYCLE",
            "SEPL_TOOL_SEED",
            "SEPL_TOOL_AUTOCOMMIT",
            "SEPL_TOOL_ROLLBACK_MARGIN",
            "SEPL_TOOL_ROLLBACK_WINDOW_HOURS",
        )
        self._orig_env = {k: os.environ.get(k) for k in self._env_keys}
        os.environ["RESOURCES_DB_PATH"] = self.db_path
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        _reset_singleton_for_tests()

    def tearDown(self) -> None:
        for k in self._env_keys:
            v = self._orig_env.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_singleton_for_tests()

    def _write_fixtures(self, name: str, fixtures: list) -> None:
        (self._fixture_dir / f"{name}.json").write_text(
            json.dumps({"tool_name": name, "fixtures": fixtures})
        )


# ── Feature flag defaults ────────────────────────────────────────────────────


class TestFeatureFlagDefaults(_EnvIsolated):
    def test_master_flag_off_by_default(self):
        os.environ.pop("SEPL_TOOL_ENABLE", None)
        self.assertFalse(tool_sepl_enabled())

    def test_dry_run_on_by_default(self):
        os.environ.pop("SEPL_TOOL_DRY_RUN", None)
        self.assertTrue(tool_sepl_dry_run())

    def test_min_margin_default(self):
        os.environ.pop("SEPL_TOOL_MIN_MARGIN", None)
        self.assertAlmostEqual(tool_sepl_min_margin(), 0.05)


# ── Helpers ──────────────────────────────────────────────────────────────────


class TestLoadParameterRanges(unittest.TestCase):
    def test_returns_valid_ranges(self):
        rec = _record(ranges={"x": {"min": 0.0, "max": 10.0, "step": 0.5}})
        ranges = _load_parameter_ranges(rec)
        self.assertEqual(ranges["x"], {"min": 0.0, "max": 10.0, "step": 0.5})

    def test_drops_invalid_entries(self):
        rec = _record(ranges={
            "x": {"min": 0, "max": 10, "step": 0.5},
            "y": {"min": 10, "max": 5, "step": 1},        # hi <= lo
            "z": {"min": 0, "max": 10, "step": 0},         # step <= 0
            "w": {"min": 0},                                # missing fields
            "v": "not a dict",                              # wrong type
        })
        ranges = _load_parameter_ranges(rec)
        self.assertIn("x", ranges)
        self.assertNotIn("y", ranges)
        self.assertNotIn("z", ranges)
        self.assertNotIn("w", ranges)
        self.assertNotIn("v", ranges)

    def test_missing_metadata_returns_empty(self):
        rec = ResourceRecord(
            name="none", kind=ResourceKind.TOOL, version="1.0.0",
            description="", learnable=True, body="", metadata={},
        )
        self.assertEqual(_load_parameter_ranges(rec), {})


# ── Select ───────────────────────────────────────────────────────────────────


class TestSelect(_EnvIsolated):
    def test_select_picks_learnable_only(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(name="a", learnable=True))
        reg.register(_record(name="b", learnable=False))
        sepl = SEPLTool(registry=reg, fixtures_dir=self._fixture_dir)
        self.assertEqual(sepl.select(["a", "b"]), "a")

    def test_select_returns_none_when_no_learnable(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(name="a", learnable=False))
        sepl = SEPLTool(registry=reg, fixtures_dir=self._fixture_dir)
        self.assertIsNone(sepl.select(["a"]))

    def test_select_ignores_unknown_tool_names(self):
        reg = ResourceRegistry(db_path=self.db_path)
        sepl = SEPLTool(registry=reg, fixtures_dir=self._fixture_dir)
        self.assertIsNone(sepl.select(["nope"]))


# ── Improve (bounded numeric perturbation) ───────────────────────────────────


class TestImprove(_EnvIsolated):
    def _mk_sepl(self, record: ResourceRecord) -> SEPLTool:
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(record)
        return SEPLTool(
            registry=reg,
            fixtures_dir=self._fixture_dir,
            rng=random.Random(7),  # deterministic
        )

    def test_candidates_are_within_ranges(self):
        rec = _record(
            config={"x": 5.0},
            fallback={"x": 5.0},
            ranges={"x": {"min": 0.0, "max": 10.0, "step": 0.5}},
        )
        sepl = self._mk_sepl(rec)
        cands = sepl.improve(rec, count=10)
        self.assertTrue(cands, "should propose at least one candidate")
        for c in cands:
            self.assertGreaterEqual(c.config["x"], 0.0)
            self.assertLessEqual(c.config["x"], 10.0)

    def test_candidates_never_invent_keys(self):
        rec = _record(config={"x": 5.0}, fallback={"x": 5.0})
        sepl = self._mk_sepl(rec)
        for c in sepl.improve(rec, count=5):
            self.assertEqual(set(c.config.keys()), {"x"})

    def test_candidates_are_distinct(self):
        rec = _record(
            config={"x": 5.0, "y": 2.0},
            fallback={"x": 5.0, "y": 2.0},
            ranges={
                "x": {"min": 0.0, "max": 10.0, "step": 0.5},
                "y": {"min": 0.0, "max": 5.0,  "step": 0.25},
            },
        )
        sepl = self._mk_sepl(rec)
        cands = sepl.improve(rec, count=5)
        sigs = {tuple(sorted(c.config.items())) for c in cands}
        self.assertEqual(len(sigs), len(cands))

    def test_empty_when_no_ranges(self):
        rec = ResourceRecord(
            name="none", kind=ResourceKind.TOOL, version="1.0.0",
            description="", learnable=True, body="",
            metadata={"config": {"x": 1.0}},  # no parameter_ranges
        )
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(rec)
        sepl = SEPLTool(registry=reg, fixtures_dir=self._fixture_dir)
        self.assertEqual(sepl.improve(rec), [])

    def test_perturbation_respects_max_steps_env(self):
        os.environ["SEPL_TOOL_MAX_PERTURB_STEPS"] = "1"
        rec = _record(
            config={"x": 5.0},
            fallback={"x": 5.0},
            ranges={"x": {"min": 0.0, "max": 10.0, "step": 0.5}},
        )
        sepl = self._mk_sepl(rec)
        # With only 1 step max, cand can only be 4.5 or 5.5
        seen = set()
        for _ in range(20):
            cands = sepl.improve(rec, count=2)
            for c in cands:
                seen.add(round(c.config["x"], 3))
        self.assertTrue(seen.issubset({4.5, 5.5}))


# ── Evaluate ─────────────────────────────────────────────────────────────────


class TestEvaluate(_EnvIsolated):
    def test_evaluate_returns_hit_rate_and_margin(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(
            name="tool_a",
            config={"threshold": 5.0},
            fallback={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))

        def handler(data, cfg):
            return 1 if data["value"] > cfg["threshold"] else 0

        self._write_fixtures("tool_a", [
            {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
            {"id": "b", "input": {"value": 4.0}, "expected": 0, "weight": 1.0},
            {"id": "c", "input": {"value": 10.0}, "expected": 1, "weight": 1.0},
        ])
        sepl = SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        # Active cfg {threshold: 5.0} is perfect on these fixtures.
        res = sepl.evaluate("tool_a", {"threshold": 5.0})
        self.assertIsNotNone(res)
        self.assertAlmostEqual(res.active_score, 1.0)
        self.assertAlmostEqual(res.candidate_score, 1.0)
        self.assertAlmostEqual(res.margin, 0.0)

    def test_evaluate_detects_regression(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(
            name="tool_a",
            config={"threshold": 5.0},
            fallback={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))

        def handler(data, cfg):
            return 1 if data["value"] > cfg["threshold"] else 0

        self._write_fixtures("tool_a", [
            {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
            {"id": "b", "input": {"value": 4.0}, "expected": 0, "weight": 1.0},
        ])
        sepl = SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        res = sepl.evaluate("tool_a", {"threshold": 7.0})
        self.assertIsNotNone(res)
        # Active nails both (1.0). Candidate flips the 6>7 case (0.5).
        self.assertAlmostEqual(res.active_score, 1.0)
        self.assertAlmostEqual(res.candidate_score, 0.5)
        self.assertAlmostEqual(res.margin, -0.5)

    def test_evaluate_missing_tool_returns_none(self):
        reg = ResourceRegistry(db_path=self.db_path)
        sepl = SEPLTool(registry=reg, fixtures_dir=self._fixture_dir)
        self.assertIsNone(sepl.evaluate("nope", {"x": 1}))

    def test_evaluate_missing_fixtures_returns_none(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(name="tool_a"))
        sepl = SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": lambda d, c: 0, "output_kind": "int"}},
        )
        self.assertIsNone(sepl.evaluate("tool_a", {"x": 1.0}))


# ── Commit ───────────────────────────────────────────────────────────────────


class TestCommit(_EnvIsolated):
    def _mk_sepl_with_handler(
        self, *, config, ranges, fixtures, handler, pinned=False, name="tool_a",
    ):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(
            name=name, config=config, fallback=dict(config),
            ranges=ranges, learnable=not pinned,
        ))
        self._write_fixtures(name, fixtures)
        return reg, SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={name: {"fn": handler, "output_kind": "int"}},
            rng=random.Random(1),
        )

    def test_low_margin_rejects(self):
        reg, sepl = self._mk_sepl_with_handler(
            config={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
            fixtures=[
                {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
                {"id": "b", "input": {"value": 4.0}, "expected": 0, "weight": 1.0},
            ],
            handler=lambda d, c: 1 if d["value"] > c["threshold"] else 0,
        )
        os.environ["SEPL_TOOL_MIN_MARGIN"] = "0.20"
        res = sepl.evaluate("tool_a", {"threshold": 5.5})
        outcome, ver = sepl.commit(
            "tool_a", res, {"threshold": 5.5}, dry_run=False, run_id="r1",
        )
        self.assertEqual(outcome, ToolSEPLOutcome.REJECTED_LOW_MARGIN)
        self.assertIsNone(ver)

    def test_dry_run_never_commits(self):
        reg, sepl = self._mk_sepl_with_handler(
            config={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
            fixtures=[
                {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
                {"id": "b", "input": {"value": 4.0}, "expected": 0, "weight": 1.0},
            ],
            handler=lambda d, c: 1 if d["value"] > c["threshold"] else 0,
        )
        # Force a pretend huge margin.
        from backend.sepl_tool import ToolEvalResult
        fake = ToolEvalResult(
            tool_name="tool_a", active_version="1.0.0",
            active_score=0.5, candidate_score=1.0, margin=0.5,
            fixtures_used=2, active_hits=1, candidate_hits=2,
        )
        outcome, ver = sepl.commit(
            "tool_a", fake, {"threshold": 5.5}, dry_run=True, run_id="r2",
        )
        self.assertEqual(outcome, ToolSEPLOutcome.DRY_RUN)
        self.assertIsNone(ver)
        # registry still has 1.0.0
        self.assertEqual(reg.active_version("tool_a"), "1.0.0")

    def test_pinned_aborts(self):
        reg, sepl = self._mk_sepl_with_handler(
            config={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
            fixtures=[
                {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
            ],
            handler=lambda d, c: 1 if d["value"] > c["threshold"] else 0,
            pinned=True,
        )
        from backend.sepl_tool import ToolEvalResult
        fake = ToolEvalResult(
            tool_name="tool_a", active_version="1.0.0",
            active_score=0.0, candidate_score=1.0, margin=1.0,
            fixtures_used=1, active_hits=0, candidate_hits=1,
        )
        outcome, ver = sepl.commit(
            "tool_a", fake, {"threshold": 5.5}, dry_run=False, run_id="r3",
        )
        self.assertEqual(outcome, ToolSEPLOutcome.ABORTED_PINNED)
        self.assertIsNone(ver)

    def test_commit_writes_new_version(self):
        reg, sepl = self._mk_sepl_with_handler(
            config={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
            fixtures=[
                {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
            ],
            handler=lambda d, c: 1 if d["value"] > c["threshold"] else 0,
        )
        from backend.sepl_tool import ToolEvalResult
        fake = ToolEvalResult(
            tool_name="tool_a", active_version="1.0.0",
            active_score=0.5, candidate_score=1.0, margin=0.5,
            fixtures_used=1, active_hits=0, candidate_hits=1,
        )
        outcome, ver = sepl.commit(
            "tool_a", fake, {"threshold": 5.5}, dry_run=False, run_id="r4",
        )
        self.assertEqual(outcome, ToolSEPLOutcome.COMMITTED)
        self.assertEqual(ver, "1.0.1")
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_rate_limit_blocks_third_commit(self):
        os.environ["SEPL_TOOL_MAX_PER_DAY"] = "2"
        reg, sepl = self._mk_sepl_with_handler(
            config={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
            fixtures=[
                {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
            ],
            handler=lambda d, c: 1 if d["value"] > c["threshold"] else 0,
        )
        from backend.sepl_tool import ToolEvalResult
        fake = ToolEvalResult(
            tool_name="tool_a", active_version="1.0.0",
            active_score=0.0, candidate_score=1.0, margin=1.0,
            fixtures_used=1, active_hits=0, candidate_hits=1,
        )
        # Two commits allowed.
        o1, _ = sepl.commit("tool_a", fake, {"threshold": 5.5}, dry_run=False, run_id="r1")
        o2, _ = sepl.commit("tool_a", fake, {"threshold": 6.0}, dry_run=False, run_id="r2")
        # Third should be rate-limited.
        o3, v3 = sepl.commit("tool_a", fake, {"threshold": 6.5}, dry_run=False, run_id="r3")
        self.assertEqual(o1, ToolSEPLOutcome.COMMITTED)
        self.assertEqual(o2, ToolSEPLOutcome.COMMITTED)
        self.assertEqual(o3, ToolSEPLOutcome.REJECTED_RATE_LIMIT)
        self.assertIsNone(v3)


# ── End-to-end run_cycle ─────────────────────────────────────────────────────


class TestRunCycle(_EnvIsolated):
    def test_disabled_aborts_before_any_work(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(name="t0"))
        sepl = SEPLTool(registry=reg, fixtures_dir=self._fixture_dir)
        os.environ.pop("SEPL_TOOL_ENABLE", None)
        report = sepl.run_cycle(["t0"])
        self.assertEqual(report.outcome, ToolSEPLOutcome.ABORTED_DISABLED)

    def test_dry_run_proposes_but_does_not_commit(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(
            name="tool_a",
            config={"threshold": 5.0},
            fallback={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))
        self._write_fixtures("tool_a", [
            {"id": "a", "input": {"value": 5.25}, "expected": 1, "weight": 1.0},
        ])
        sepl = SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": lambda d, c: 1 if d["value"] > c["threshold"] else 0,
                                  "output_kind": "int"}},
            rng=random.Random(11),
        )
        os.environ["SEPL_TOOL_DRY_RUN"] = "1"
        report = sepl.run_cycle(["tool_a"], force_enable=True)
        # Either REJECTED_UNCHANGED (no improving candidate) or DRY_RUN.
        self.assertIn(report.outcome, (
            ToolSEPLOutcome.DRY_RUN, ToolSEPLOutcome.REJECTED_UNCHANGED,
            ToolSEPLOutcome.REJECTED_LOW_MARGIN,
        ))
        self.assertEqual(reg.active_version("tool_a"), "1.0.0")

    def test_cycle_commits_when_margin_is_positive_and_dry_off(self):
        reg = ResourceRegistry(db_path=self.db_path)
        # Active is close to ideal but off: threshold=6.5 gets 3/4 right.
        # Some candidate near threshold=5 will get 4/4 right, beating margin.
        reg.register(_record(
            name="tool_a",
            config={"threshold": 6.5},
            fallback={"threshold": 6.5},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))
        self._write_fixtures("tool_a", [
            {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
            {"id": "b", "input": {"value": 4.0}, "expected": 0, "weight": 1.0},
            {"id": "c", "input": {"value": 7.0}, "expected": 1, "weight": 1.0},
            {"id": "d", "input": {"value": 3.0}, "expected": 0, "weight": 1.0},
        ])
        sepl = SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": lambda d, c: 1 if d["value"] > c["threshold"] else 0,
                                  "output_kind": "int"}},
            rng=random.Random(3),
        )
        os.environ["SEPL_TOOL_DRY_RUN"] = "0"
        os.environ["SEPL_TOOL_MIN_MARGIN"] = "0.05"
        os.environ["SEPL_TOOL_CANDIDATES_PER_CYCLE"] = "20"
        report = sepl.run_cycle(["tool_a"], force_enable=True)
        self.assertEqual(report.outcome, ToolSEPLOutcome.COMMITTED)
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")
        self.assertGreater(report.eval.margin, 0.0)

    def test_no_handler_aborts(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(name="unknown_tool"))
        sepl = SEPLTool(registry=reg, fixtures_dir=self._fixture_dir, handlers={})
        report = sepl.run_cycle(["unknown_tool"], force_enable=True)
        self.assertEqual(report.outcome, ToolSEPLOutcome.ABORTED_NO_HANDLER)

    def test_no_fixtures_aborts(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(name="tool_a"))
        sepl = SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": lambda d, c: 0, "output_kind": "int"}},
        )
        # Ranges present, handler present, but no JSON file written.
        report = sepl.run_cycle(["tool_a"], force_enable=True)
        self.assertEqual(report.outcome, ToolSEPLOutcome.ABORTED_NO_FIXTURES)


# ── Integration with real seeded tier-0 tools ────────────────────────────────


class TestRunCycleOnRealTools(_EnvIsolated):
    """Run SEPL against the three YAML-seeded tier-0 tools and their real
    fixtures. We verify the cycle runs, never crashes, and — because the
    defaults are already 100% on their own fixtures — any commit requires a
    strict improvement that does not exist. So we expect REJECTED_LOW_MARGIN
    or REJECTED_UNCHANGED, NOT an accidental mutation."""

    def setUp(self) -> None:
        super().setUp()
        seed_resources_if_empty()
        self._fixture_dir = (
            Path(__file__).resolve().parent.parent / "resources" / "sepl_eval_fixtures_tools"
        )

    def test_cycle_does_not_regress_canonical_tools(self):
        """With active fixtures at 100% hit rate, no candidate can strictly
        beat it → cycle must not commit even when fully enabled."""
        os.environ["SEPL_TOOL_DRY_RUN"] = "0"
        os.environ["SEPL_TOOL_MIN_MARGIN"] = "0.01"
        os.environ["SEPL_TOOL_CANDIDATES_PER_CYCLE"] = "8"
        # Tier-1 is capped at 1/day by default; SEPL's cycle never commits for
        # macro_vix_to_credit_stress because the canonical config is perfect
        # on its fixtures, BUT we still want the tier gate to never raise.
        os.environ["SEPL_TOOL_MAX_PER_DAY_TIER_1"] = "1"
        sepl = SEPLTool(fixtures_dir=self._fixture_dir, rng=random.Random(42))
        for tool_name in (
            "short_interest_classifier",
            "debate_stance_heuristic_bull",
            "debate_stance_heuristic_bear",
            "macro_vix_to_credit_stress",
        ):
            report = sepl.run_cycle([tool_name], force_target=tool_name, force_enable=True)
            self.assertIn(
                report.outcome,
                (
                    ToolSEPLOutcome.REJECTED_LOW_MARGIN,
                    ToolSEPLOutcome.REJECTED_UNCHANGED,
                ),
                f"{tool_name} unexpectedly committed: {report.outcome}",
            )
            from backend.resource_registry import get_resource_registry
            active = get_resource_registry().active_version(tool_name)
            self.assertEqual(active, "1.0.0", f"{tool_name} active bumped unexpectedly")


# ── Kill switch ──────────────────────────────────────────────────────────────


class TestKillSwitchTunables(_EnvIsolated):
    def test_autocommit_defaults_off(self):
        os.environ.pop("SEPL_TOOL_AUTOCOMMIT", None)
        self.assertFalse(tool_sepl_autocommit())

    def test_rollback_margin_default(self):
        os.environ.pop("SEPL_TOOL_ROLLBACK_MARGIN", None)
        self.assertAlmostEqual(tool_sepl_rollback_margin(), 0.05)

    def test_rollback_margin_clamped(self):
        os.environ["SEPL_TOOL_ROLLBACK_MARGIN"] = "99"
        self.assertAlmostEqual(tool_sepl_rollback_margin(), 1.0)

    def test_rollback_window_default(self):
        os.environ.pop("SEPL_TOOL_ROLLBACK_WINDOW_HOURS", None)
        self.assertEqual(tool_sepl_rollback_window_hours(), 168)


class TestKillSwitchBehaviour(_EnvIsolated):
    """Build a realistic scenario: v1.0.0 default, SEPL commits v1.0.1 that
    regresses fixtures. Kill switch should roll back to v1.0.0 when autocommit
    is on, or only report when it is off."""

    def _seed_with_regression(self, *, fresh: bool = True):
        """Register tool_a @1.0.0 (good config), then SEPL-commit 1.0.1 (bad
        config). Returns (reg, sepl, handler, fixtures)."""
        reg = ResourceRegistry(db_path=self.db_path)

        # Good default: threshold=5.0 perfectly splits fixtures.
        reg.register(_record(
            name="tool_a",
            version="1.0.0",
            config={"threshold": 5.0},
            fallback={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))

        fixtures = [
            {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
            {"id": "b", "input": {"value": 4.0}, "expected": 0, "weight": 1.0},
            {"id": "c", "input": {"value": 7.0}, "expected": 1, "weight": 1.0},
            {"id": "d", "input": {"value": 3.0}, "expected": 0, "weight": 1.0},
        ]
        self._write_fixtures("tool_a", fixtures)

        def handler(data, cfg):
            return 1 if data["value"] > cfg["threshold"] else 0

        sepl = SEPLTool(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
            rng=random.Random(5),
        )

        # Commit a BAD candidate directly via the real SEPL commit path so the
        # lineage row carries actor="sepl:tool" and operation="update".
        from backend.sepl_tool import ToolEvalResult
        bad_cfg = {"threshold": 9.5}  # accepts nothing; gets 50% on the set
        bad_eval = ToolEvalResult(
            tool_name="tool_a", active_version="1.0.0",
            active_score=0.5, candidate_score=1.0, margin=0.5,
            fixtures_used=len(fixtures), active_hits=2, candidate_hits=4,
        )
        os.environ["SEPL_TOOL_MIN_MARGIN"] = "0.05"
        os.environ["SEPL_TOOL_MAX_PER_DAY"] = "10"
        outcome, ver = sepl.commit(
            "tool_a", bad_eval, bad_cfg, dry_run=False, run_id="regress",
        )
        assert outcome == ToolSEPLOutcome.COMMITTED, outcome
        assert ver == "1.0.1"
        return reg, handler, fixtures

    def test_reports_regression_but_no_restore_when_autocommit_off(self):
        reg, handler, _ = self._seed_with_regression()
        os.environ.pop("SEPL_TOOL_AUTOCOMMIT", None)  # off by default
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        # dry_run=None → falls back to autocommit (off) → effective dry.
        report = kill.check("tool_a")
        self.assertEqual(report.outcome, ToolRollbackOutcome.DRY_RUN)
        self.assertEqual(report.committed_version, "1.0.1")
        self.assertEqual(report.prior_version, "1.0.0")
        self.assertLess(report.delta, 0.0)
        # Registry is UNCHANGED — still pointing at the (bad) 1.0.1.
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_restores_prior_version_when_autocommit_on(self):
        reg, handler, _ = self._seed_with_regression()
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        report = kill.check("tool_a")
        self.assertEqual(report.outcome, ToolRollbackOutcome.ROLLED_BACK)
        self.assertEqual(report.restored_to_version, "1.0.0")
        self.assertEqual(reg.active_version("tool_a"), "1.0.0")

    def test_dry_run_arg_overrides_autocommit(self):
        reg, handler, _ = self._seed_with_regression()
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"  # would restore…
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        report = kill.check("tool_a", dry_run=True)  # …but caller forces dry
        self.assertEqual(report.outcome, ToolRollbackOutcome.DRY_RUN)
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_ok_within_tolerance_when_regression_is_small(self):
        reg, handler, _ = self._seed_with_regression()
        # Force a massive margin so the real delta (-0.5) falls below it.
        os.environ["SEPL_TOOL_ROLLBACK_MARGIN"] = "0.99"
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        report = kill.check("tool_a")
        self.assertEqual(report.outcome, ToolRollbackOutcome.OK_WITHIN_TOLERANCE)
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_no_recent_commit_when_never_committed(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(
            name="tool_a",
            config={"threshold": 5.0}, fallback={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))
        self._write_fixtures("tool_a", [
            {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
        ])
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": lambda d, c: 0, "output_kind": "int"}},
        )
        report = kill.check("tool_a")
        self.assertEqual(report.outcome, ToolRollbackOutcome.NO_RECENT_SEPL_COMMIT)
        self.assertEqual(reg.active_version("tool_a"), "1.0.0")

    def test_skips_non_sepl_commits(self):
        """A manual actor must NOT trigger the kill switch."""
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_record(
            name="tool_a",
            config={"threshold": 5.0}, fallback={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))
        self._write_fixtures("tool_a", [
            {"id": "a", "input": {"value": 6.0}, "expected": 1, "weight": 1.0},
        ])
        # Manual human update.
        from backend.tool_configs import update_tool_config
        update_tool_config(
            "tool_a", {"threshold": 9.9},
            reason="human tweak", actor="alice",
        )
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": lambda d, c: 0, "output_kind": "int"}},
        )
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"
        report = kill.check("tool_a")
        # Human commit is ignored — no SEPL commit found.
        self.assertEqual(report.outcome, ToolRollbackOutcome.NO_RECENT_SEPL_COMMIT)
        # Registry still shows human-set 1.0.1, not rolled back.
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_skips_sepl_commits_outside_window(self):
        reg, handler, _ = self._seed_with_regression()
        os.environ["SEPL_TOOL_ROLLBACK_WINDOW_HOURS"] = "1"
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
            # Pretend it's 1 week later → commit is outside 1h window.
            now_fn=lambda: __import__("time").time() + 7 * 86400,
        )
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"
        report = kill.check("tool_a")
        self.assertEqual(report.outcome, ToolRollbackOutcome.NO_RECENT_SEPL_COMMIT)
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_no_fixtures_surface_as_outcome(self):
        reg, handler, _ = self._seed_with_regression()
        # Delete the fixture file under the same dir the kill switch reads.
        (self._fixture_dir / "tool_a.json").unlink()
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"
        report = kill.check("tool_a")
        self.assertEqual(report.outcome, ToolRollbackOutcome.NO_FIXTURES)
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_no_handler_surface_as_outcome(self):
        reg, _, _ = self._seed_with_regression()
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={},  # no handler registered
        )
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"
        report = kill.check("tool_a")
        self.assertEqual(report.outcome, ToolRollbackOutcome.NO_HANDLER)
        self.assertEqual(reg.active_version("tool_a"), "1.0.1")

    def test_rollback_commit_does_not_trigger_itself(self):
        """After a rollback, the restored version must NOT itself be picked
        up as a recent sepl:tool 'update'. Restores use operation='restore'."""
        reg, handler, _ = self._seed_with_regression()
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        os.environ["SEPL_TOOL_AUTOCOMMIT"] = "1"

        first = kill.check("tool_a")
        self.assertEqual(first.outcome, ToolRollbackOutcome.ROLLED_BACK)

        # Immediately re-check: there's no new SEPL commit since, so this must
        # be a no-op, NOT a second rollback, NOT an infinite loop.
        second = kill.check("tool_a")
        self.assertIn(
            second.outcome,
            (
                ToolRollbackOutcome.NO_RECENT_SEPL_COMMIT,
                ToolRollbackOutcome.OK_WITHIN_TOLERANCE,
            ),
        )
        self.assertEqual(reg.active_version("tool_a"), "1.0.0")

    def test_check_all_iterates_learnable_only(self):
        reg, handler, _ = self._seed_with_regression()
        reg.register(_record(
            name="tool_pinned",
            version="1.0.0",
            learnable=False,
            config={"threshold": 5.0}, fallback={"threshold": 5.0},
            ranges={"threshold": {"min": 1.0, "max": 10.0, "step": 0.5}},
        ))
        kill = SEPLToolKillSwitch(
            registry=reg, fixtures_dir=self._fixture_dir,
            handlers={"tool_a": {"fn": handler, "output_kind": "int"}},
        )
        reports = kill.check_all()
        names = {r.tool_name for r in reports}
        self.assertIn("tool_a", names)
        self.assertNotIn("tool_pinned", names)


if __name__ == "__main__":
    unittest.main()
