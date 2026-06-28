"""Offline tests for page_feedback persistence and API."""
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.auth import UserInfo, get_current_admin_user, get_optional_user
from backend.main import app


class PageFeedbackTestBase(unittest.TestCase):
    def setUp(self):
        from backend import page_feedback as pf

        self.pf = pf
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "progress.db")
        pf.DB_PATH = self.db_path
        if hasattr(pf._local, "conn"):
            pf._local.conn.close()
            delattr(pf._local, "conn")
        pf.init_page_feedback_db()

    def tearDown(self):
        if hasattr(self.pf._local, "conn"):
            self.pf._local.conn.close()
            delattr(self.pf._local, "conn")
        self.tmp.cleanup()


class TestPageFeedbackStore(PageFeedbackTestBase):
    @patch("backend.page_feedback._use_postgres", return_value=False)
    def test_save_and_summary(self, _pg):
        fid = self.pf.save_feedback(
            user_id="u1",
            page="/dashboard",
            rating=5,
            comment="Great breakdown",
            symbol="NVDA",
        )
        self.assertTrue(fid.startswith("pf_"))
        rows = self.pf.feedback_summary()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["page"], "/dashboard")
        self.assertEqual(rows[0]["submission_count"], 1)
        self.assertAlmostEqual(rows[0]["avg_rating"], 5.0)
        self.assertEqual(rows[0]["comment_count"], 1)

    @patch("backend.page_feedback._use_postgres", return_value=False)
    def test_rejects_empty_submission(self, _pg):
        with self.assertRaises(ValueError):
            self.pf.save_feedback(user_id=None, page="/macro", rating=None, comment=None)

    @patch("backend.page_feedback._use_postgres", return_value=False)
    def test_rejects_invalid_rating(self, _pg):
        with self.assertRaises(ValueError):
            self.pf.save_feedback(user_id=None, page="/macro", rating=6)


class TestPageFeedbackApi(PageFeedbackTestBase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        app.dependency_overrides[get_optional_user] = lambda: None
        app.dependency_overrides[get_current_admin_user] = lambda: UserInfo(
            id="admin1", email="admin@test.com", name="Admin", avatar=""
        )

    def tearDown(self):
        app.dependency_overrides.pop(get_optional_user, None)
        app.dependency_overrides.pop(get_current_admin_user, None)
        super().tearDown()

    @patch("backend.page_feedback._use_postgres", return_value=False)
    def test_post_feedback_anonymous(self, _pg):
        res = self.client.post(
            "/page-feedback",
            json={"page": "/dashboard", "rating": 4, "comment": "Helpful"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body.get("ok"))
        self.assertTrue(str(body.get("id", "")).startswith("pf_"))

    @patch("backend.page_feedback._use_postgres", return_value=False)
    def test_post_rejects_empty_body(self, _pg):
        res = self.client.post("/page-feedback", json={"page": "/dashboard"})
        self.assertEqual(res.status_code, 422)

    @patch("backend.page_feedback._use_postgres", return_value=False)
    def test_admin_summary(self, _pg):
        self.pf.save_feedback(user_id=None, page="/chat", rating=3)
        res = self.client.get("/page-feedback/summary")
        self.assertEqual(res.status_code, 200)
        pages = res.json().get("pages") or []
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page"], "/chat")


if __name__ == "__main__":
    unittest.main()
