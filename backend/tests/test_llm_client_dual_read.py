"""
Tests for ``llm_client`` dual-read: prompts come from the RSPL registry
when present, else fall back byte-exactly to the hardcoded dict.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend import resource_registry as rr  # noqa: E402
from backend.resource_seeder import seed_resources_if_empty  # noqa: E402
from backend.llm_client import LLMClient, AGENT_SYSTEM_PROMPTS  # noqa: E402


class _DualReadBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = os.path.join(self._tmp.name, "r.db")
        os.environ["RESOURCES_DB_PATH"] = db_path
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        rr._reset_singleton_for_tests()
        self.reg = rr.get_resource_registry()
        seed_resources_if_empty(self.reg)
        self.client = LLMClient()

    def tearDown(self):
        rr._reset_singleton_for_tests()
        os.environ.pop("RESOURCES_DB_PATH", None)


class TestResolveSystemPrompt(_DualReadBase):
    def test_every_role_resolves_to_seeded_v1(self):
        for role, legacy_body in AGENT_SYSTEM_PROMPTS.items():
            body, version = self.client._resolve_system_prompt(role)
            self.assertEqual(body, legacy_body, f"body drift for role={role}")
            self.assertEqual(version, "1.0.0", f"unexpected version {version} for {role}")

    def test_flag_off_uses_legacy_dict(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        try:
            for role, legacy_body in AGENT_SYSTEM_PROMPTS.items():
                body, version = self.client._resolve_system_prompt(role)
                self.assertEqual(body, legacy_body)
                self.assertEqual(version, "unversioned")
        finally:
            os.environ["RESOURCES_USE_REGISTRY"] = "1"

    def test_unknown_role_returns_default(self):
        body, version = self.client._resolve_system_prompt("does_not_exist_anywhere")
        self.assertIn("finance analyst", body)
        self.assertEqual(version, "unversioned")

    def test_update_shifts_active_version(self):
        # Human updates the bull prompt → active version bumps; dual-read sees new version
        self.reg.update(
            "bull", "new body", bump="minor", reason="unit test", actor="human:test"
        )
        body, version = self.client._resolve_system_prompt("bull")
        self.assertEqual(version, "1.1.0")
        self.assertEqual(body, "new body")

    def test_pinned_resource_still_resolves(self):
        # moderator is pinned (learnable=False) but still readable
        body, version = self.client._resolve_system_prompt("moderator")
        self.assertIn("investment committee chair", body)
        self.assertEqual(version, "1.0.0")


class TestGenerateWithMeta(_DualReadBase):
    def test_meta_contains_prompt_name_and_version(self):
        async def run():
            return await self.client.generate_with_meta("bull", "hi")

        result, meta = asyncio.run(run())
        self.assertEqual(meta["prompt_name"], "bull")
        self.assertEqual(meta["prompt_version"], "1.0.0")
        self.assertIsInstance(result, dict)

    def test_legacy_generate_still_returns_plain_dict(self):
        async def run():
            return await self.client.generate("bear", "hi")

        out = asyncio.run(run())
        self.assertIsInstance(out, dict)
        # Must match shape of hardcoded fallback template (no regression)
        self.assertIn("headline", out)
        self.assertIn("key_points", out)

    def test_meta_version_unversioned_when_flag_off(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        try:

            async def run():
                return await self.client.generate_with_meta("bull", "hi")

            _, meta = asyncio.run(run())
            self.assertEqual(meta["prompt_version"], "unversioned")
        finally:
            os.environ["RESOURCES_USE_REGISTRY"] = "1"


class TestRegistryOutageSafe(unittest.TestCase):
    """If the registry raises, llm_client must never propagate the error."""

    def test_resolve_falls_back_on_registry_exception(self):
        client = LLMClient()
        # Sabotage the singleton getter so it raises
        original = rr.get_resource_registry

        def _boom():
            raise RuntimeError("simulated registry outage")

        try:
            rr.get_resource_registry = _boom  # type: ignore[assignment]
            body, version = client._resolve_system_prompt("bull")
            self.assertEqual(body, AGENT_SYSTEM_PROMPTS["bull"])
            self.assertEqual(version, "unversioned")
        finally:
            rr.get_resource_registry = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
