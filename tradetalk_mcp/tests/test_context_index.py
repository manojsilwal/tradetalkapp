"""Generated context index smoke tests."""

from __future__ import annotations

import json
import os
import unittest


class TestContextIndex(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.path = os.path.join(self.repo_root, ".tradetalk", "context-index.json")

    def test_file_exists_and_has_routers(self) -> None:
        self.assertTrue(os.path.isfile(self.path), "run scripts/generate_context_index.py")
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("generated_at", data)
        self.assertTrue(data.get("backend", {}).get("routers"))


if __name__ == "__main__":
    unittest.main()
