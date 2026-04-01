"""Unit tests for agent_memory (SQLite + optional vector hooks)."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from backend import agent_memory


class TestAgentMemory(unittest.TestCase):
    def setUp(self):
        self._orig_path = agent_memory.DB_PATH
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        agent_memory.DB_PATH = self.tmp.name
        # Fresh connection per test path
        if hasattr(agent_memory._local, "agent_mem_conn"):
            delattr(agent_memory._local, "agent_mem_conn")

    def tearDown(self):
        agent_memory.DB_PATH = self._orig_path
        if hasattr(agent_memory._local, "agent_mem_conn"):
            try:
                agent_memory._local.agent_mem_conn.close()
            except Exception:
                pass
            delattr(agent_memory._local, "agent_mem_conn")
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_init_save_load_roundtrip(self):
        agent_memory.init_agent_memory_db()
        ks = MagicMock()
        agent_memory.save_memory(ks, "u1", "s1", "user", "Hello")
        agent_memory.save_memory(ks, "u1", "s1", "assistant", "Hi there")
        rows = agent_memory.load_memory("u1", "s1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[0]["content"], "Hello")
        self.assertEqual(rows[1]["role"], "assistant")

    def test_save_memory_calls_embedding_when_semantic_summary(self):
        agent_memory.init_agent_memory_db()
        ks = MagicMock()
        agent_memory.save_memory(
            ks,
            "u1",
            "s1",
            "assistant",
            "Reply text",
            semantic_summary="User: Hi\nAssistant: Reply text",
            tickers=["AAPL"],
            topic="chat",
        )
        ks.add_chat_memory.assert_called_once()
        args, kwargs = ks.add_chat_memory.call_args
        self.assertEqual(args[0], "u1")
        self.assertIn("User: Hi", args[2])

    def test_search_memory_delegates(self):
        ks = MagicMock()
        ks.query_chat_memories.return_value = ["past fact"]
        out = agent_memory.search_memory(ks, "u1", "occupation", n_results=3)
        self.assertEqual(out, ["past fact"])
        ks.query_chat_memories.assert_called_once_with("u1", "occupation", n_results=3)

    def test_format_memory_context_block_empty(self):
        self.assertEqual(agent_memory.format_memory_context_block([]), "")


if __name__ == "__main__":
    unittest.main()
