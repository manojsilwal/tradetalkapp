"""Regression: macro_flow qual fetch must import asyncio."""
import unittest

from backend.macro_flow import qual_node_agent


class TestQualNodeAgentImports(unittest.TestCase):
    def test_asyncio_imported_for_to_thread(self):
        self.assertTrue(hasattr(qual_node_agent, "asyncio"))


if __name__ == "__main__":
    unittest.main()
