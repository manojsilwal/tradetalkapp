"""Tests for chat session history API (authenticated users)."""
import os
import tempfile

# Initialize temporary database path before importing any backend modules
_tmp_dir = tempfile.TemporaryDirectory()
_db_path = os.path.join(_tmp_dir.name, "progress.db")
os.environ["PROGRESS_DB_PATH"] = _db_path

import unittest
from fastapi.testclient import TestClient

from backend import agent_memory
from backend.auth import _issue_jwt, init_users_db, upsert_user
from backend.main import app


class TestChatHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Clean up any cached thread-local connections to the legacy database
        from backend import auth as auth_mod
        from backend import agent_memory as am_mod
        if hasattr(auth_mod._local, "conn"):
            try:
                auth_mod._local.conn.close()
            except Exception:
                pass
            delattr(auth_mod._local, "conn")
        if hasattr(am_mod._local, "conn"):
            try:
                am_mod._local.conn.close()
            except Exception:
                pass
            delattr(am_mod._local, "conn")

        init_users_db()
        agent_memory.init_agent_memory_db()
        upsert_user("hist_user_1", "hist@example.com", "Hist User", "")
        cls.client = TestClient(app)
        cls.token = _issue_jwt("hist_user_1")
        cls.headers = {"Authorization": f"Bearer {cls.token}"}
        cls.session_id = "sess_hist_test_001"
        agent_memory.save_memory(
            None,
            "hist_user_1",
            cls.session_id,
            "user",
            "What is AAPL doing today?",
        )
        agent_memory.save_memory(
            None,
            "hist_user_1",
            cls.session_id,
            "assistant",
            "AAPL is trading near recent highs.",
            semantic_summary="User asked about AAPL",
        )

    @classmethod
    def tearDownClass(cls):
        from backend import auth as auth_mod
        from backend import agent_memory as am_mod
        if hasattr(auth_mod._local, "conn"):
            try:
                auth_mod._local.conn.close()
            except Exception:
                pass
            delattr(auth_mod._local, "conn")
        if hasattr(am_mod._local, "conn"):
            try:
                am_mod._local.conn.close()
            except Exception:
                pass
            delattr(am_mod._local, "conn")

        os.environ.pop("PROGRESS_DB_PATH", None)
        _tmp_dir.cleanup()

    def test_list_sessions_requires_auth(self):
        res = self.client.get("/chat/sessions")
        self.assertEqual(res.status_code, 401)

    def test_list_sessions_returns_summary(self):
        res = self.client.get("/chat/sessions", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["user_id"], "hist_user_1")
        sessions = data.get("sessions") or []
        self.assertTrue(any(s["session_id"] == self.session_id for s in sessions))
        match = next(s for s in sessions if s["session_id"] == self.session_id)
        self.assertIn("AAPL", match.get("title", ""))
        self.assertGreaterEqual(match.get("message_count", 0), 2)

    def test_get_session_transcript(self):
        res = self.client.get(
            f"/chat/sessions/{self.session_id}",
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["session_id"], self.session_id)
        msgs = data.get("messages") or []
        self.assertGreaterEqual(len(msgs), 2)
        roles = [m["role"] for m in msgs]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_get_session_transcript_wrong_user_404(self):
        upsert_user("other_hist_user", "other_hist@example.com", "Other", "")
        other_token = _issue_jwt("other_hist_user")
        res = self.client.get(
            f"/chat/sessions/{self.session_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(res.status_code, 404)

    def test_list_sessions_helper(self):
        rows = agent_memory.list_sessions("hist_user_1")
        self.assertTrue(any(r["session_id"] == self.session_id for r in rows))

    def test_session_belongs_to_user(self):
        self.assertTrue(agent_memory.session_belongs_to_user("hist_user_1", self.session_id))
        self.assertFalse(agent_memory.session_belongs_to_user("other_hist_user", self.session_id))


if __name__ == "__main__":
    unittest.main()
