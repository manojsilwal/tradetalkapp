"""Action registry tests."""

from __future__ import annotations

import os
import unittest

from tradetalk_mcp.action_registry import load_action_registry


class TestRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def test_load_actions(self) -> None:
        reg = load_action_registry(self.repo_root)
        names = {a.name for a in reg.actions}
        self.assertIn("health_check_backend", names)
        self.assertIn("run_backtest", names)
        by = reg.by_name()
        self.assertEqual(by["run_backtest"].mutates, True)


if __name__ == "__main__":
    unittest.main()
