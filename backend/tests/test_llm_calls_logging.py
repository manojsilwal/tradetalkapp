import os
import tempfile
import unittest
import sqlite3
import time
from backend.migrations.runner import run_migrations
from backend.decision_ledger import SQLiteLedgerBackend, log_llm_api_call, get_ledger, set_ledger_for_tests


class TestLlmCallsLogging(unittest.TestCase):
    """Test suites for LLM API calls logging and cost calculation."""

    def setUp(self):
        # Create a temp DB and initialize the SQLite ledger backend with it
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        # Run migrations on the temp db
        run_migrations(self.db_path, "decisions")
        
        self.test_backend = SQLiteLedgerBackend(db_path=self.db_path)
        set_ledger_for_tests(self.test_backend)

    def tearDown(self):
        set_ledger_for_tests(None)
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_llm_api_calls_table_exists(self):
        """Verify the migration applied and table exists."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='llm_api_calls'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "llm_api_calls")
        conn.close()

    def test_log_llm_api_call_inserts_row(self):
        """Verify calling log_llm_api_call logs query details, latency, and estimated cost."""
        prompt = "Explain PE ratio in 1 paragraph."
        model = "google/gemini-3.5-flash"
        latency = 1.45
        response = "The PE ratio is price divided by earnings per share."
        
        # Call logging helper
        log_llm_api_call(
            prompt_text=prompt,
            model=model,
            latency=latency,
            response_text=response,
            prompt_tokens=100,
            completion_tokens=200
        )
        
        # Verify call details in database
        calls = self.test_backend.list_llm_calls(limit=10)
        self.assertEqual(len(calls), 1)
        
        logged_call = calls[0]
        self.assertEqual(logged_call["query_brief"], prompt)
        self.assertEqual(logged_call["llm_used"], model)
        self.assertEqual(logged_call["time_taken"], latency)
        
        # Expected Cost for Gemini 3.5 Flash:
        # In rate: $0.075 / 1M = 0.000000075 USD/token
        # Out rate: $0.30 / 1M = 0.00000030 USD/token
        # Cost = 100 * 0.000000075 + 200 * 0.00000030 = 0.0000075 + 0.00006 = 0.0000675
        self.assertAlmostEqual(logged_call["cost"], 0.0000675, places=8)
        self.assertTrue(time.time() - logged_call["timestamp"] < 5.0)

    def test_log_llm_api_call_token_approximation(self):
        """Verify token counts and cost are estimated correctly when token parameters are 0."""
        prompt = "Short prompt text." # 18 chars -> approx 4 tokens
        model = "deepseek-ai/deepseek-v4-pro"
        latency = 0.8
        response = "Short response." # 15 chars -> approx 3 tokens
        
        log_llm_api_call(
            prompt_text=prompt,
            model=model,
            latency=latency,
            response_text=response,
            prompt_tokens=0,
            completion_tokens=0
        )
        
        calls = self.test_backend.list_llm_calls(limit=10)
        self.assertEqual(len(calls), 1)
        
        logged_call = calls[0]
        self.assertEqual(logged_call["query_brief"], prompt)
        self.assertEqual(logged_call["llm_used"], model)
        self.assertEqual(logged_call["time_taken"], latency)
        # Cost must be non-zero (calculated via approximation)
        self.assertGreater(logged_call["cost"], 0.0)

    def test_query_brief_length_cap(self):
        """Verify that very long prompts are capped to 120 characters in the query_brief."""
        long_prompt = "A" * 200
        model = "google/gemini-3.5-flash"
        
        log_llm_api_call(
            prompt_text=long_prompt,
            model=model,
            latency=0.5
        )
        
        calls = self.test_backend.list_llm_calls(limit=10)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]["query_brief"]), 120)
        self.assertTrue(calls[0]["query_brief"].endswith("..."))


if __name__ == "__main__":
    unittest.main()
