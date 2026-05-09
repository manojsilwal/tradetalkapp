"""Path confinement tests."""

from __future__ import annotations

import os
import unittest

from tradetalk_mcp.security.paths import PathSecurityError, read_text_capped, resolve_under_root


class TestPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def test_traversal_rejected(self) -> None:
        with self.assertRaises(PathSecurityError):
            resolve_under_root(self.repo_root, "../etc/passwd")

    def test_absolute_rejected(self) -> None:
        with self.assertRaises(PathSecurityError):
            resolve_under_root(self.repo_root, "/etc/passwd")

    def test_read_readme(self) -> None:
        text = read_text_capped(self.repo_root, "README.md", 50_000)
        self.assertIn("TradeTalk", text)


if __name__ == "__main__":
    unittest.main()
