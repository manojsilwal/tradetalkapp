"""Meta-harness JSON snapshot (no LLM)."""
import os
import tempfile
import time
import unittest

import backend.user_preferences as user_preferences
from backend import claim_store
from backend import coral_hub
from backend.meta_harness.report import build_meta_harness_report


class TestMetaHarnessReport(unittest.TestCase):
    def setUp(self):
        self._orig_up = user_preferences.DB_PATH
        self._orig_coral = coral_hub.DB_PATH
        claim_store.reset_thread_local_connection()
        coral_hub.reset_thread_local_connection()

    def tearDown(self):
        user_preferences.DB_PATH = self._orig_up
        coral_hub.DB_PATH = self._orig_coral
        claim_store.reset_thread_local_connection()
        coral_hub.reset_thread_local_connection()

    def test_build_report_shape(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            user_preferences.DB_PATH = path
            coral_hub.DB_PATH = path
            claim_store.reset_thread_local_connection()
            coral_hub.reset_thread_local_connection()
            coral_hub.init_coral_hub_db()
            claim_store.init_claim_store_db()

            coral_hub.log_handoff_event("handoff_test", {"ok": True})
            coral_hub.record_attempt("t1", "a1", 1.0, 0.5)
            claim_store.add_claim_for_symbol("AAPL", "Test claim for harness.")

            rep = build_meta_harness_report(since_days=1.0)
            self.assertEqual(rep.get("schema_version"), 1)
            self.assertGreaterEqual(rep["handoff_events"]["count"], 1)
            self.assertGreaterEqual(rep["coral_attempts"]["count"], 1)
            cs = rep.get("claim_store") or {}
            self.assertNotIn("error", cs)
            self.assertGreaterEqual(cs.get("active_claims", 0), 1)
            now = time.time()
            self.assertLessEqual(float(rep["since_epoch"]), now)
            self.assertAlmostEqual(float(rep["since_epoch"]), now - 86400.0, delta=3.0)
        finally:
            os.unlink(path)
