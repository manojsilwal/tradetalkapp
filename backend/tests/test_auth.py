"""Tests for authentication — JWT, dev mode, user management."""
import os
import unittest
import time

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.auth import (
    _issue_jwt, _decode_jwt, upsert_user, get_user,
    login_with_google, DEV_MODE, init_users_db,
)


class TestJWT(unittest.TestCase):
    """JWT issuance and decode round-trip."""

    def test_issue_and_decode_jwt(self):
        token = _issue_jwt("test_user_123")
        self.assertIsInstance(token, str)
        self.assertTrue(len(token) > 10)
        user_id = _decode_jwt(token)
        self.assertEqual(user_id, "test_user_123")

    def test_decode_invalid_token_raises(self):
        with self.assertRaises(ValueError):
            _decode_jwt("totally.invalid.token")

    def test_decode_empty_raises(self):
        with self.assertRaises(ValueError):
            _decode_jwt("")


class TestDevModeLogin(unittest.TestCase):
    """Dev mode login creates a test user."""

    def setUp(self):
        init_users_db()

    def test_dev_login_returns_token(self):
        if not DEV_MODE:
            self.skipTest("Not in DEV_MODE")
        result = login_with_google("dev")
        self.assertIn("token", result)
        self.assertEqual(result["user_id"], "dev_user_001")
        self.assertEqual(result["email"], "dev@tradetalk.local")
        self.assertTrue(result["dev_mode"])

    def test_dev_login_token_is_decodable(self):
        if not DEV_MODE:
            self.skipTest("Not in DEV_MODE")
        result = login_with_google("dev")
        user_id = _decode_jwt(result["token"])
        self.assertEqual(user_id, "dev_user_001")


class TestUserPersistence(unittest.TestCase):
    """User upsert and retrieval."""

    def setUp(self):
        init_users_db()

    def test_upsert_and_get(self):
        user = upsert_user("g_123", "test@example.com", "Test User", "https://avatar.url")
        self.assertEqual(user.id, "g_123")
        self.assertEqual(user.email, "test@example.com")

        fetched = get_user("g_123")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.email, "test@example.com")

    def test_get_nonexistent_returns_none(self):
        result = get_user("nonexistent_user_xyz")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
