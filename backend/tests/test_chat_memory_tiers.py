"""Tests for durable chat sessions + financial preference tools."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.chat_service import ChatSession
from backend import chat_session_store as css
from backend.chat_session_store import (
    apply_stored_payload,
    init_chat_sessions_db,
    load_session_row,
    save_session_row,
    user_matches_row,
)
from backend.user_preferences import (
    FINANCIAL_TOOL_KEYS,
    save_financial_preference_for_tool,
)


class TestChatSessionPayloadRoundTrip(unittest.TestCase):
    def test_serialize_apply_preserves_fields(self):
        s = ChatSession(
            session_id="sid",
            system_prompt="ignored",
            assembled_at=100.0,
            expires_at=200.0,
            user_id="u1",
        )
        s.sticky_state = {"active_ticker": "XOM", "turn_count": 2}
        s.rag_prewarm = {"gold": "ctx"}
        s.last_user_message = "hi"
        s.last_assistant_text = "yo"
        s.last_evidence_contract = {"tools_called": ["get_stock_quote"]}
        s.last_chat_meta = {"k": 1}

        payload = json.loads(css._serialize_payload(s))
        t = ChatSession(
            session_id="sid",
            system_prompt="",
            assembled_at=100.0,
            expires_at=200.0,
            user_id="u1",
        )
        apply_stored_payload(t, payload)
        self.assertEqual(t.sticky_state["active_ticker"], "XOM")
        self.assertEqual(t.rag_prewarm.get("gold"), "ctx")
        self.assertEqual(t.last_user_message, "hi")
        self.assertEqual(t.last_evidence_contract.get("tools_called"), ["get_stock_quote"])


class TestChatSessionStoreSqlite(unittest.TestCase):
    def setUp(self):
        self._mem = sqlite3.connect(":memory:")
        self._mem.row_factory = sqlite3.Row

        def fake_conn():
            return self._mem

        self._patcher = patch("backend.chat_session_store._get_conn", fake_conn)
        self._patcher.start()
        init_chat_sessions_db()

    def tearDown(self):
        self._patcher.stop()
        self._mem.close()

    def test_save_load_row(self):
        s = ChatSession(
            session_id="abc",
            system_prompt="",
            assembled_at=1.0,
            expires_at=9999999999.0,
            user_id="user-1",
        )
        s.sticky_state = {"analysis_mode": "gold"}
        save_session_row("abc", "user-1", 1.0, 9999999999.0, s)
        row = load_session_row("abc")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["user_id"], "user-1")
        self.assertEqual(row["payload"].get("sticky_state", {}).get("analysis_mode"), "gold")


class TestUserMatchesRow(unittest.TestCase):
    def test_both_none(self):
        self.assertTrue(user_matches_row(None, None))

    def test_mismatch(self):
        self.assertFalse(user_matches_row("a", "b"))


class TestFinancialPreferenceTool(unittest.TestCase):
    def test_invalid_key(self):
        out = save_financial_preference_for_tool("u1", "not_a_key", "x")
        self.assertIn("Unknown key", out)

    def test_invalid_value(self):
        out = save_financial_preference_for_tool("u1", "risk_tolerance", "nope")
        self.assertIn("Invalid value", out)

    def test_financial_tool_keys_cover_enums(self):
        self.assertIn("position_type", FINANCIAL_TOOL_KEYS)
        self.assertIn("trading_style", FINANCIAL_TOOL_KEYS)


if __name__ == "__main__":
    unittest.main()
