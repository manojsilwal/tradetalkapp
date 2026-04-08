"""Phase C minimal claim store (SQLite)."""
import os
import tempfile
import unittest

import backend.user_preferences as user_preferences
from backend import claim_store


class TestClaimStore(unittest.TestCase):
    def setUp(self):
        self._orig_path = user_preferences.DB_PATH
        claim_store.reset_thread_local_connection()

    def tearDown(self):
        user_preferences.DB_PATH = self._orig_path
        claim_store.reset_thread_local_connection()

    def test_entity_and_claim_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            user_preferences.DB_PATH = path
            claim_store.reset_thread_local_connection()
            claim_store.init_claim_store_db()
            cid = claim_store.add_claim_for_symbol(
                "MSFT",
                "Revenue growth exceeded expectations in FY25 Q2.",
                source_ref="sec:10-K",
                confidence=0.82,
            )
            self.assertGreater(cid, 0)
            rows = claim_store.list_claims_for_symbol("msft", n=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "MSFT")
            self.assertIn("Revenue growth", rows[0]["claim_text"])
            st = claim_store.stats()
            self.assertEqual(st["entities"], 1)
            self.assertEqual(st["active_claims"], 1)
        finally:
            os.unlink(path)
