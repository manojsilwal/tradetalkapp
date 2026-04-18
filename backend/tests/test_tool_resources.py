"""
Tests for Phase C1 PR 1 — TOOL resource kind in the RSPL registry.

Covers:
    * YAML seeding of the three tier-0 tools
    * Dual-read helper (registry on / off / malformed / missing)
    * ``update_tool_config`` schema guards
    * ``learnable=False`` pinning
    * Lineage stamping
    * Byte-exact fallback when flag is off (regression guard for
      ``agents.py::ShortInterestAgentPair`` and ``debate_agents.py``).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import backend.resource_registry as rr_mod  # noqa: E402
from backend.resource_registry import (  # noqa: E402
    ResourceKind,
    ResourceNotFoundError,
    ResourcePinnedError,
    ResourceRecord,
    ResourceRegistry,
    _reset_singleton_for_tests,
    registry_enabled,
)
from backend.resource_seeder import (  # noqa: E402
    TOOLS_DIR,
    discover_tool_files,
    discover_all_resource_files,
    _yaml_to_record,
    seed_resources_if_empty,
)
from backend.tool_configs import (  # noqa: E402
    get_tool_config,
    get_tool_config_with_version,
    update_tool_config,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


def _tool_record(
    name: str = "t0",
    *,
    version: str = "1.0.0",
    learnable: bool = True,
    config: dict | None = None,
    fallback: dict | None = None,
) -> ResourceRecord:
    config = config if config is not None else {"x": 1.0}
    fallback = fallback if fallback is not None else {"x": 1.0}
    return ResourceRecord(
        name=name,
        kind=ResourceKind.TOOL,
        version=version,
        description=f"{name} desc",
        learnable=learnable,
        body="# handler docstring placeholder",
        schema={"type": "object", "properties": {"x": {"type": "number"}}},
        fallback=fallback,
        metadata={"config": dict(config), "tier": 0, "pure": True},
    )


class _TempRegistryTestCase(unittest.TestCase):
    """Shared helper: each test uses an isolated sqlite file and singleton."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "r.db")
        # Drop any module-level singleton so get_resource_registry() picks up
        # our env-overridden path.
        _reset_singleton_for_tests()
        self._orig_db_env = os.environ.get("RESOURCES_DB_PATH")
        os.environ["RESOURCES_DB_PATH"] = self.db_path
        self._orig_flag_env = os.environ.get("RESOURCES_USE_REGISTRY")
        os.environ["RESOURCES_USE_REGISTRY"] = "1"

    def tearDown(self) -> None:
        _reset_singleton_for_tests()
        if self._orig_db_env is None:
            os.environ.pop("RESOURCES_DB_PATH", None)
        else:
            os.environ["RESOURCES_DB_PATH"] = self._orig_db_env
        if self._orig_flag_env is None:
            os.environ.pop("RESOURCES_USE_REGISTRY", None)
        else:
            os.environ["RESOURCES_USE_REGISTRY"] = self._orig_flag_env


# ── YAML seeding ─────────────────────────────────────────────────────────────


class TestToolYamlSeedingShape(unittest.TestCase):
    """These tests do not hit the DB — they verify the on-disk YAML is
    well-formed and parses into a TOOL ResourceRecord."""

    def test_tool_dir_exists(self):
        self.assertTrue(TOOLS_DIR.is_dir(), f"Expected {TOOLS_DIR} to exist")

    def test_three_expected_tier0_tools_on_disk(self):
        names = sorted(p.stem for p in discover_tool_files())
        self.assertIn("short_interest_classifier", names)
        self.assertIn("debate_stance_heuristic_bull", names)
        self.assertIn("debate_stance_heuristic_bear", names)
        self.assertIn("macro_vix_to_credit_stress", names)

    def test_all_discovered_files_parse(self):
        for path in discover_tool_files():
            rec = _yaml_to_record(path)
            self.assertEqual(rec.kind, ResourceKind.TOOL, f"{path.name}")
            self.assertTrue(rec.learnable, f"{path.name} should be learnable")
            self.assertIsInstance(rec.metadata.get("config"), dict, path.name)
            self.assertIsInstance(rec.fallback, dict, path.name)

    def test_config_is_flat_numeric(self):
        for path in discover_tool_files():
            rec = _yaml_to_record(path)
            for key, value in (rec.metadata.get("config") or {}).items():
                self.assertIsInstance(
                    value, (int, float),
                    f"{rec.name}.{key}={value!r} must be numeric",
                )

    def test_fallback_matches_config_keys(self):
        for path in discover_tool_files():
            rec = _yaml_to_record(path)
            self.assertEqual(
                set((rec.fallback or {}).keys()),
                set((rec.metadata.get("config") or {}).keys()),
                f"{rec.name}: fallback and config must have identical keys",
            )

    def test_discover_all_includes_tools(self):
        all_files = {p.name for p in discover_all_resource_files()}
        self.assertIn("short_interest_classifier.yaml", all_files)
        self.assertIn("bull.yaml", all_files)  # prompt from Phase A still present


# ── Dual-read helper ─────────────────────────────────────────────────────────


class TestGetToolConfig(_TempRegistryTestCase):
    def test_registry_off_returns_default(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        self.assertFalse(registry_enabled())
        out = get_tool_config("t0", {"x": 7.0})
        self.assertEqual(out, {"x": 7.0})

    def test_registry_on_but_missing_returns_default(self):
        out = get_tool_config("nope", {"x": 7.0})
        self.assertEqual(out, {"x": 7.0})

    def test_registry_on_returns_registered_config(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 99.5}, fallback={"x": 0.0}))
        out = get_tool_config("t0", {"x": 7.0})
        self.assertEqual(out, {"x": 99.5})

    def test_wrong_kind_falls_back_to_default(self):
        reg = ResourceRegistry(db_path=self.db_path)
        # Register a PROMPT with same name — dual-read must decline it.
        reg.register(
            ResourceRecord(
                name="t0",
                kind=ResourceKind.PROMPT,
                version="1.0.0",
                description="",
                learnable=True,
                body="some prompt",
            )
        )
        with self.assertLogs("backend.tool_configs", level="WARNING"):
            out = get_tool_config("t0", {"x": 7.0})
        self.assertEqual(out, {"x": 7.0})

    def test_missing_key_in_registry_is_filled_from_default(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 99.5}, fallback={"x": 0.0}))
        out = get_tool_config("t0", {"x": 7.0, "y": 42.0})
        self.assertEqual(out, {"x": 99.5, "y": 42.0})

    def test_extra_key_in_registry_is_ignored(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 99.5, "sneaky": 1.0}, fallback={"x": 0.0}))
        out = get_tool_config("t0", {"x": 7.0})
        self.assertEqual(out, {"x": 99.5})
        self.assertNotIn("sneaky", out)

    def test_result_is_copy(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 99.5}, fallback={"x": 0.0}))
        out = get_tool_config("t0", {"x": 7.0})
        out["x"] = -1.0
        out2 = get_tool_config("t0", {"x": 7.0})
        self.assertEqual(out2["x"], 99.5)

    def test_default_must_be_dict(self):
        with self.assertRaises(TypeError):
            get_tool_config("t0", ["not", "a", "dict"])  # type: ignore[arg-type]

    def test_version_is_reported(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", version="2.3.4", config={"x": 1.0}, fallback={"x": 0.0}))
        cfg, ver = get_tool_config_with_version("t0", {"x": 7.0})
        self.assertEqual(ver, "2.3.4")
        self.assertEqual(cfg, {"x": 1.0})


# ── update_tool_config ───────────────────────────────────────────────────────


class TestUpdateToolConfig(_TempRegistryTestCase):
    def test_update_bumps_version_and_writes_lineage(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", version="1.0.0", config={"x": 1.0}, fallback={"x": 1.0}))
        updated = update_tool_config(
            "t0", {"x": 2.0}, reason="test bump", actor="tester"
        )
        self.assertEqual(updated.version, "1.0.1")
        self.assertEqual(updated.metadata["config"], {"x": 2.0})

        lineage = reg.lineage("t0")
        ops = [row["operation"] for row in lineage]
        self.assertIn("update", ops)

    def test_update_refuses_pinned(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", learnable=False))
        with self.assertRaises(ResourcePinnedError):
            update_tool_config("t0", {"x": 2.0}, reason="r", actor="a")

    def test_update_refuses_missing_tool(self):
        with self.assertRaises(ResourceNotFoundError):
            update_tool_config("nope", {"x": 2.0}, reason="r", actor="a")

    def test_update_refuses_extra_keys(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 1.0}, fallback={"x": 1.0}))
        with self.assertRaises(ValueError) as ctx:
            update_tool_config("t0", {"x": 2.0, "y": 3.0}, reason="r", actor="a")
        self.assertIn("unknown keys", str(ctx.exception))

    def test_update_refuses_missing_keys(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(
            _tool_record(name="t0", config={"x": 1.0, "y": 2.0}, fallback={"x": 1.0, "y": 2.0})
        )
        with self.assertRaises(ValueError) as ctx:
            update_tool_config("t0", {"x": 5.0}, reason="r", actor="a")
        self.assertIn("missing keys", str(ctx.exception))

    def test_update_refuses_non_numeric(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 1.0}, fallback={"x": 1.0}))
        with self.assertRaises(ValueError):
            update_tool_config("t0", {"x": "two"}, reason="r", actor="a")  # type: ignore[dict-item]
        with self.assertRaises(ValueError):
            update_tool_config("t0", {"x": True}, reason="r", actor="a")

    def test_update_refuses_empty_reason_or_actor(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 1.0}, fallback={"x": 1.0}))
        with self.assertRaises(ValueError):
            update_tool_config("t0", {"x": 2.0}, reason="", actor="a")
        with self.assertRaises(ValueError):
            update_tool_config("t0", {"x": 2.0}, reason="r", actor="")

    def test_updated_config_is_served_by_get_tool_config(self):
        reg = ResourceRegistry(db_path=self.db_path)
        reg.register(_tool_record(name="t0", config={"x": 1.0}, fallback={"x": 1.0}))
        update_tool_config("t0", {"x": 42.0}, reason="r", actor="a")
        cfg = get_tool_config("t0", {"x": 0.0})
        self.assertEqual(cfg, {"x": 42.0})


# ── YAML -> DB full seeding ──────────────────────────────────────────────────


class TestSeedingRealTools(_TempRegistryTestCase):
    def test_seeding_registers_all_tier0_tools(self):
        summary = seed_resources_if_empty()
        names = {entry.split("@")[0] for entry in summary["inserted"]}
        self.assertIn("short_interest_classifier", names)
        self.assertIn("debate_stance_heuristic_bull", names)
        self.assertIn("debate_stance_heuristic_bear", names)

    def test_seeded_short_interest_classifier_matches_hardcoded_defaults(self):
        """Byte-exact regression guard: if the YAML ever drifts from the old
        hardcoded numbers in agents.py, this test fails before any live user
        sees the drift."""
        seed_resources_if_empty()
        cfg = get_tool_config(
            "short_interest_classifier",
            {
                "sir_bull_threshold": 15.0,
                "sir_ambiguous_min": 10.0,
                "sir_ambiguous_max": 20.0,
                "dtc_confirm_threshold": 5.0,
                "bearish_csi_threshold": 1.1,
            },
        )
        self.assertEqual(cfg["sir_bull_threshold"], 15.0)
        self.assertEqual(cfg["sir_ambiguous_min"], 10.0)
        self.assertEqual(cfg["sir_ambiguous_max"], 20.0)
        self.assertEqual(cfg["dtc_confirm_threshold"], 5.0)
        self.assertEqual(cfg["bearish_csi_threshold"], 1.1)

    def test_seeded_debate_bull_matches_hardcoded_defaults(self):
        seed_resources_if_empty()
        cfg = get_tool_config("debate_stance_heuristic_bull", {
            "sir_bull_floor": 5.0,
            "rev_growth_bull_floor": 15.0,
            "r3m_bull_floor": 5.0,
            "sir_bear_ceiling": 2.0,
            "rev_growth_bear_ceiling": 0.0,
            "r3m_bear_ceiling": -10.0,
        })
        self.assertEqual(cfg["sir_bull_floor"], 5.0)
        self.assertEqual(cfg["rev_growth_bull_floor"], 15.0)

    def test_seeded_debate_bear_matches_hardcoded_defaults(self):
        seed_resources_if_empty()
        cfg = get_tool_config("debate_stance_heuristic_bear", {
            "pe_bear_threshold": 50.0,
            "debt_eq_bear_threshold": 200.0,
            "r3m_bear_ceiling": -15.0,
            "pe_bull_ceiling": 20.0,
            "r3m_bull_floor": 0.0,
        })
        self.assertEqual(cfg["pe_bear_threshold"], 50.0)
        self.assertEqual(cfg["debt_eq_bear_threshold"], 200.0)

    def test_reseeding_is_idempotent(self):
        seed_resources_if_empty()
        second = seed_resources_if_empty()
        # Second run should insert nothing new.
        self.assertEqual(second["inserted"], [])


if __name__ == "__main__":
    unittest.main()
