"""Mode / action gating."""

from __future__ import annotations

import unittest
from tradetalk_mcp.config import Settings
from tradetalk_mcp.security.permissions import ActionGate


def _settings(**kwargs) -> Settings:
    base = dict(
        repo_root="/tmp",
        mode="context",
        api_base_url="http://127.0.0.1:8000",
        openapi_url="http://127.0.0.1:8000/openapi.json",
        actions_enabled=False,
        api_key="",
        max_read_bytes=10000,
        log_level="WARNING",
        dry_run=False,
        rate_limit_ms=0,
        api_host_allowlist=frozenset({"127.0.0.1"}),
    )
    base.update(kwargs)
    return Settings(**base)


class TestPermissions(unittest.TestCase):
    def test_context_hides_service_visibility_check(self) -> None:
        g = ActionGate(_settings(mode="context"))
        self.assertFalse(g.service_tools_visible())

    def test_mutating_requires_actions_enabled(self) -> None:
        g = ActionGate(_settings(mode="full", actions_enabled=False))
        ok, msg = g.can_call_action(mutates=True, requires_actions_enabled=True)
        self.assertFalse(ok)
        self.assertIn("ACTIONS_ENABLED", msg)

    def test_readonly_ok_in_full_without_actions_flag(self) -> None:
        g = ActionGate(_settings(mode="full", actions_enabled=False))
        ok, _ = g.can_call_action(mutates=False, requires_actions_enabled=False)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
