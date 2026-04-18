"""Tests for the RSPL resource registry and YAML seeder (Phase A)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.resource_registry import (  # noqa: E402
    ResourceKind,
    ResourceNotFoundError,
    ResourcePinnedError,
    ResourceRecord,
    ResourceRegistry,
    _bump_semver,
    _parse_semver,
)
from backend.resource_seeder import (  # noqa: E402
    SeedError,
    _yaml_to_record,
    seed_resources_if_empty,
)


class TestSemver(unittest.TestCase):
    def test_parse_happy_path(self):
        self.assertEqual(_parse_semver("1.2.3"), (1, 2, 3))

    def test_parse_rejects_bad(self):
        for bad in ("1.2", "a.b.c", "", "1.2.3.4", "-1.0.0"):
            with self.assertRaises(ValueError):
                _parse_semver(bad)

    def test_bump(self):
        self.assertEqual(_bump_semver("1.2.3", "patch"), "1.2.4")
        self.assertEqual(_bump_semver("1.2.3", "minor"), "1.3.0")
        self.assertEqual(_bump_semver("1.2.3", "major"), "2.0.0")
        with self.assertRaises(ValueError):
            _bump_semver("1.2.3", "nope")  # type: ignore[arg-type]


class _TempRegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "r.db")
        self.reg = ResourceRegistry(db_path=self.db_path)

    def _mk(
        self,
        name: str = "foo",
        *,
        version: str = "1.0.0",
        learnable: bool = True,
        body: str = "hello",
        schema=None,
        fallback=None,
    ) -> ResourceRecord:
        return ResourceRecord(
            name=name,
            kind=ResourceKind.PROMPT,
            version=version,
            description=f"{name} desc",
            learnable=learnable,
            body=body,
            schema=schema,
            fallback=fallback,
        )


class TestRegistryLifecycle(_TempRegistryTestCase):
    def test_register_sets_active_and_lineage(self):
        rec = self.reg.register(self._mk())
        self.assertEqual(rec.version, "1.0.0")
        self.assertEqual(self.reg.active_version("foo"), "1.0.0")
        lineage = self.reg.lineage("foo")
        self.assertEqual(len(lineage), 1)
        self.assertEqual(lineage[0]["operation"], "register")
        self.assertIsNone(lineage[0]["from_version"])
        self.assertEqual(lineage[0]["to_version"], "1.0.0")

    def test_register_idempotent_same_version(self):
        self.reg.register(self._mk())
        # Second call with same (name, version) must be a no-op, not an error
        self.reg.register(self._mk())
        self.assertEqual(self.reg.versions("foo"), ["1.0.0"])
        # Only one lineage row — idempotent shortcut returns early before lineage
        self.assertEqual(len(self.reg.lineage("foo")), 1)

    def test_register_rejects_bad_semver(self):
        with self.assertRaises(ValueError):
            self.reg.register(self._mk(version="bad"))

    def test_update_bumps_and_records_lineage(self):
        self.reg.register(self._mk())
        updated = self.reg.update(
            "foo",
            "new body",
            bump="minor",
            reason="test",
            actor="human:unit",
        )
        self.assertEqual(updated.version, "1.1.0")
        self.assertEqual(updated.body, "new body")
        self.assertEqual(self.reg.active_version("foo"), "1.1.0")
        ops = [e["operation"] for e in self.reg.lineage("foo")]
        self.assertEqual(ops, ["update", "register"])  # newest first

    def test_update_rejects_pinned(self):
        self.reg.register(self._mk(learnable=False))
        with self.assertRaises(ResourcePinnedError):
            self.reg.update("foo", "x", bump="patch", reason="r", actor="a")
        # Still only the register lineage row
        self.assertEqual([e["operation"] for e in self.reg.lineage("foo")], ["register"])

    def test_update_rejects_missing(self):
        with self.assertRaises(ResourceNotFoundError):
            self.reg.update("missing", "x", bump="patch", reason="r", actor="a")

    def test_update_requires_reason_and_actor(self):
        self.reg.register(self._mk())
        with self.assertRaises(ValueError):
            self.reg.update("foo", "x", bump="patch", reason="", actor="a")
        with self.assertRaises(ValueError):
            self.reg.update("foo", "x", bump="patch", reason="r", actor="")

    def test_restore_flips_active_without_new_row(self):
        self.reg.register(self._mk(version="1.0.0"))
        self.reg.update("foo", "b", bump="patch", reason="r", actor="a")  # -> 1.0.1
        self.reg.update("foo", "c", bump="patch", reason="r", actor="a")  # -> 1.0.2
        self.assertEqual(self.reg.active_version("foo"), "1.0.2")
        self.reg.restore("foo", "1.0.0", reason="revert", actor="human:unit")
        self.assertEqual(self.reg.active_version("foo"), "1.0.0")
        self.assertEqual(self.reg.get("foo").body, "hello")
        # Rows: register(1.0.0), update(1.0.1), update(1.0.2), restore -> 1.0.0
        self.assertEqual(
            [e["operation"] for e in self.reg.lineage("foo")],
            ["restore", "update", "update", "register"],
        )
        self.assertEqual(self.reg.versions("foo"), ["1.0.2", "1.0.1", "1.0.0"])

    def test_restore_rejects_unknown_version(self):
        self.reg.register(self._mk())
        with self.assertRaises(ResourceNotFoundError):
            self.reg.restore("foo", "9.9.9", reason="r", actor="a")


class TestRegistryQueries(_TempRegistryTestCase):
    def test_list_filters_by_kind(self):
        self.reg.register(self._mk("a"))
        self.reg.register(self._mk("b", learnable=False))
        recs = self.reg.list(ResourceKind.PROMPT)
        names = sorted(r.name for r in recs)
        self.assertEqual(names, ["a", "b"])
        self.assertEqual(self.reg.list(ResourceKind.TOOL), [])

    def test_list_returns_active_only(self):
        self.reg.register(self._mk("a"))
        self.reg.update("a", "body2", bump="minor", reason="r", actor="h")
        active = self.reg.list(ResourceKind.PROMPT)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].body, "body2")

    def test_get_with_explicit_version(self):
        self.reg.register(self._mk("a", version="1.0.0", body="v1"))
        self.reg.update("a", "v2", bump="patch", reason="r", actor="h")
        self.assertEqual(self.reg.get("a", "1.0.0").body, "v1")
        self.assertEqual(self.reg.get("a").body, "v2")
        self.assertIsNone(self.reg.get("nope"))

    def test_load_contract(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        fallback = {"x": 0}
        self.reg.register(self._mk("a", schema=schema, fallback=fallback))
        contract = self.reg.load_contract("a")
        self.assertEqual(contract["schema"], schema)
        self.assertEqual(contract["fallback"], fallback)
        self.assertEqual(contract["version"], "1.0.0")

    def test_snapshot_id_is_deterministic_and_changes_on_update(self):
        self.reg.register(self._mk("a"))
        sid1 = self.reg.snapshot_id()
        self.assertEqual(sid1, self.reg.snapshot_id())
        self.reg.update("a", "x", bump="patch", reason="r", actor="h")
        self.assertNotEqual(sid1, self.reg.snapshot_id())


class TestYamlSeed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.reg = ResourceRegistry(db_path=os.path.join(self._tmp.name, "r.db"))

    def test_seed_is_idempotent(self):
        s1 = seed_resources_if_empty(self.reg)
        self.assertGreater(len(s1["inserted"]), 0, "expected YAML files to be seeded")
        s2 = seed_resources_if_empty(self.reg)
        self.assertEqual(s2["inserted"], [], "second run must insert nothing")
        self.assertEqual(len(s2["skipped"]), s1["total_yaml"])

    def test_every_prompt_role_has_record(self):
        # Byte-for-byte check: every role referenced by llm_client has a record
        from backend.llm_client import AGENT_SYSTEM_PROMPTS
        seed_resources_if_empty(self.reg)
        for role, body in AGENT_SYSTEM_PROMPTS.items():
            rec = self.reg.get(role)
            self.assertIsNotNone(rec, f"missing resource record for role {role!r}")
            self.assertEqual(rec.body, body, f"body drift for role {role!r}")

    def test_learnable_policy_for_phase_a(self):
        seed_resources_if_empty(self.reg)
        pinned_expected = {
            "moderator",
            "swarm_synthesizer",
            "gold_advisor",
            "strategy_parser",
            "backtest_explainer",
            "decision_terminal_roadmap",
        }
        for name in pinned_expected:
            rec = self.reg.get(name)
            self.assertIsNotNone(rec, f"{name} missing")
            self.assertFalse(
                rec.learnable,
                f"{name} must be pinned (learnable=False) in Phase A — safety surface",
            )
        # Representative learnables
        for name in ("bull", "bear", "macro", "swarm_analyst", "swarm_reflection_writer"):
            self.assertTrue(self.reg.get(name).learnable, f"{name} must be learnable in Phase A")

    def test_yaml_validation_rejects_missing_fields(self):
        bad = Path(self._tmp.name) / "bad.yaml"
        bad.write_text("name: x\nkind: prompt\n", encoding="utf-8")
        with self.assertRaises(SeedError):
            _yaml_to_record(bad)

    def test_yaml_validation_rejects_bad_kind(self):
        bad = Path(self._tmp.name) / "bad2.yaml"
        bad.write_text(
            "name: x\nkind: bogus\nversion: 1.0.0\nbody: hi\n", encoding="utf-8"
        )
        with self.assertRaises(SeedError):
            _yaml_to_record(bad)

    def test_yaml_validation_rejects_bad_semver(self):
        bad = Path(self._tmp.name) / "bad3.yaml"
        bad.write_text(
            "name: x\nkind: prompt\nversion: notsemver\nbody: hi\n", encoding="utf-8"
        )
        with self.assertRaises(SeedError):
            _yaml_to_record(bad)

    def test_yaml_does_not_overwrite_human_update(self):
        """If a human updates a resource, a later reseed must NOT revert."""
        seed_resources_if_empty(self.reg)
        # Pick a learnable seeded record and bump it
        self.reg.update(
            "bull", "custom human body", bump="patch", reason="test", actor="human:unit"
        )
        self.assertEqual(self.reg.active_version("bull"), "1.0.1")
        # Reseed should not touch active pointer
        summary = seed_resources_if_empty(self.reg)
        self.assertEqual(summary["inserted"], [])
        self.assertEqual(self.reg.active_version("bull"), "1.0.1")
        self.assertEqual(self.reg.get("bull").body, "custom human body")


class TestRegistrySerialization(_TempRegistryTestCase):
    def test_schema_and_fallback_round_trip(self):
        schema = {"type": "object", "required": ["x"]}
        fallback = {"x": 1, "nested": {"a": [1, 2, 3]}}
        self.reg.register(self._mk(schema=schema, fallback=fallback))
        rec = self.reg.get("foo")
        self.assertEqual(rec.schema, schema)
        self.assertEqual(rec.fallback, fallback)

    def test_snapshot_shape(self):
        self.reg.register(self._mk("a"))
        snap = self.reg.snapshot()
        self.assertEqual(snap["count"], 1)
        self.assertIn("records", snap)
        self.assertEqual(snap["records"][0]["name"], "a")
        json.dumps(snap)  # must be JSON-serializable


if __name__ == "__main__":
    unittest.main()
