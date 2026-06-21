"""Tests for authentication — JWT, dev mode, Google signup, OTP, password setup."""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.auth import (
    _issue_jwt,
    _decode_jwt,
    upsert_user,
    get_user,
    login_with_google,
    init_users_db,
    signup_with_google,
    complete_set_password,
    initiate_login_with_password,
    complete_login_with_otp,
    user_has_password,
    user_is_admin,
    UserInfo,
    ADMIN_EMAILS,
)
from backend import auth as auth_mod


def _use_temp_auth_db(testcase: unittest.TestCase) -> None:
    testcase._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    testcase._tmp.close()
    testcase._db_path = testcase._tmp.name
    os.environ["PROGRESS_DB_PATH"] = testcase._db_path
    
    def cleanup():
        os.environ.pop("PROGRESS_DB_PATH", None)
        try:
            os.unlink(testcase._db_path)
        except OSError:
            pass
    testcase.addCleanup(cleanup)
    
    auth_mod.DB_PATH = testcase._db_path
    if hasattr(auth_mod._local, "conn"):
        delattr(auth_mod._local, "conn")
    init_users_db()


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
        _use_temp_auth_db(self)

    @patch("backend.auth.DEV_MODE", True)
    def test_dev_login_returns_token(self) -> None:
        result = login_with_google("dev")
        self.assertIn("token", result)
        self.assertEqual(result["user_id"], "dev_user_001")
        self.assertEqual(result["email"], "dev@tradetalk.local")
        self.assertTrue(result["dev_mode"])

    @patch("backend.auth.DEV_MODE", True)
    def test_dev_login_token_is_decodable(self) -> None:
        result = login_with_google("dev")
        user_id = _decode_jwt(result["token"])
        self.assertEqual(user_id, "dev_user_001")

    @patch("backend.auth.DEV_MODE", True)
    def test_dev_user_is_admin_in_dev_mode(self) -> None:
        result = login_with_google("dev")
        self.assertTrue(result["is_admin"])
        user = get_user(result["user_id"])
        self.assertTrue(user_is_admin(user))


class TestUserPersistence(unittest.TestCase):
    """User upsert and retrieval."""

    def setUp(self):
        _use_temp_auth_db(self)

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


class TestGoogleSignupAndOtp(unittest.TestCase):
    def setUp(self):
        _use_temp_auth_db(self)

    @patch("backend.auth.DEV_MODE", True)
    def test_google_signup_returns_setup_token(self):
        result = signup_with_google("dev")
        self.assertTrue(result["needs_password"])
        self.assertIn("setup_token", result)
        self.assertEqual(result["email"], "dev@tradetalk.local")
        self.assertFalse(user_has_password("dev_user_001"))

    @patch("backend.auth.DEV_MODE", True)
    def test_set_password_after_google_signup(self):
        signup = signup_with_google("dev")
        complete_set_password(signup["setup_token"], "securepass123")
        self.assertTrue(user_has_password("dev_user_001"))

    @patch("backend.auth.DEV_MODE", True)
    def test_google_signup_409_when_password_exists(self):
        signup = signup_with_google("dev")
        complete_set_password(signup["setup_token"], "securepass123")
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            signup_with_google("dev")
        self.assertEqual(ctx.exception.status_code, 409)

    @patch("backend.auth.DEV_MODE", True)
    @patch("backend.email_otp.RESEND_API_KEY", "")
    def test_login_otp_round_trip(self):
        signup = signup_with_google("dev")
        complete_set_password(signup["setup_token"], "securepass123")
        step1 = initiate_login_with_password("dev@tradetalk.local", "securepass123")
        self.assertIn("otp_session_id", step1)
        self.assertTrue(step1["otp_dev_bypass"])
        session = complete_login_with_otp(step1["otp_session_id"], "123456")
        self.assertIn("token", session)
        self.assertEqual(session["user_id"], "dev_user_001")
        self.assertTrue(session["has_password"])

    @patch("backend.auth.DEV_MODE", True)
    @patch("backend.email_otp.RESEND_API_KEY", "")
    def test_wrong_password_rejected(self):
        signup = signup_with_google("dev")
        complete_set_password(signup["setup_token"], "securepass123")
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            initiate_login_with_password("dev@tradetalk.local", "wrongpass")
        self.assertEqual(ctx.exception.status_code, 401)

    @patch("backend.auth.DEV_MODE", True)
    @patch("backend.email_otp.RESEND_API_KEY", "")
    def test_user_progress_persists_after_otp_login(self):
        from backend.user_progress import award_xp, get_progress, init_db

        init_db()
        signup = signup_with_google("dev")
        complete_set_password(signup["setup_token"], "securepass123")
        step1 = initiate_login_with_password("dev@tradetalk.local", "securepass123")
        session = complete_login_with_otp(step1["otp_session_id"], "654321")
        user_id = session["user_id"]
        award_xp(user_id, "debate", note="smoke")
        progress = get_progress(user_id)
        self.assertGreater(progress["xp"], 0)
        self.assertIsNotNone(get_user(user_id))


class TestAdminAccess(unittest.TestCase):
    def test_default_admin_email(self):
        self.assertIn("silwal.saroj44@gmail.com", ADMIN_EMAILS)

    def test_admin_user_recognized(self):
        admin = UserInfo(
            id="google_sub_123",
            email="silwal.saroj44@gmail.com",
            name="Saroj",
            avatar="",
        )
        self.assertTrue(user_is_admin(admin))

    def test_non_admin_rejected(self):
        user = UserInfo(
            id="other_user",
            email="someone@example.com",
            name="Other",
            avatar="",
        )
        self.assertFalse(user_is_admin(user))

    def test_admin_flag_in_session_payload(self):
        _use_temp_auth_db(self)
        admin = upsert_user("admin_001", "silwal.saroj44@gmail.com", "Admin User", "")
        from backend.auth import _user_session_payload
        payload = _user_session_payload(admin)
        self.assertTrue(payload["is_admin"])
        self.assertFalse(_user_session_payload(
            UserInfo(id="x", email="other@example.com", name="Other", avatar="")
        )["is_admin"])

    def test_get_current_admin_user_rejects_non_admin(self):
        from fastapi import HTTPException
        from backend.auth import get_current_admin_user, UserInfo

        non_admin = UserInfo(id="x", email="other@example.com", name="Other", avatar="")
        with self.assertRaises(HTTPException) as ctx:
            get_current_admin_user(non_admin)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
