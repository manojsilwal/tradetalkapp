"""Integration tests for the /sepl/* HTTP surface (Phase B, PR 5)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402


class TestStatusEndpoint(unittest.TestCase):
    """/sepl/status must always be safe to call — flag state + tunables only."""

    def setUp(self):
        self.client = TestClient(app)

    def test_status_when_disabled(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "0"}, clear=False):
            r = self.client.get("/sepl/status")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertFalse(body["enabled"])
            self.assertIn("min_samples", body["tunables"])
            self.assertIn("min_margin", body["tunables"])
            self.assertIn("max_commits_per_day", body["tunables"])
            self.assertIn("effectiveness_ceiling", body["tunables"])
            self.assertIn("fixtures_dir", body)

    def test_status_when_enabled(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.get("/sepl/status")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["enabled"])


class TestGatingBehaviour(unittest.TestCase):
    """Every live endpoint must refuse when SEPL_ENABLE != 1."""

    def setUp(self):
        self.client = TestClient(app)

    def test_select_preview_blocked_when_disabled(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "0"}, clear=False):
            r = self.client.get("/sepl/select/preview")
            self.assertEqual(r.status_code, 503)
            self.assertIn("disabled", r.json()["detail"].lower())

    def test_run_blocked_when_disabled(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "0"}, clear=False):
            r = self.client.post("/sepl/run", json={})
            self.assertEqual(r.status_code, 503)


class TestRunEndpoint(unittest.TestCase):
    """POST /sepl/run must honor belt-and-suspenders guards."""

    def setUp(self):
        self.client = TestClient(app)

    def test_run_defaults_to_dry_run_when_commit_false(self):
        """Without ``commit: true``, a live request must still be dry-run."""
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.post("/sepl/run", json={"dry_run": False})
            # With no reflections in the live knowledge_store, outcome should be
            # ABORTED_INSUFFICIENT_DATA or similar — but dry_run MUST be True
            # regardless because commit=False.
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["dry_run"], f"commit=False must force dry_run=True, got {body}")

    def test_run_honors_explicit_dry_run_true(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.post("/sepl/run", json={"dry_run": True, "commit": True})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["dry_run"])

    def test_run_returns_expected_schema(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.post("/sepl/run", json={})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            # Required fields present
            for key in ("run_id", "outcome", "dry_run", "elapsed_sec"):
                self.assertIn(key, body, f"missing {key} in {body}")
            self.assertIsInstance(body["run_id"], str)
            self.assertTrue(len(body["run_id"]) >= 8)

    def test_run_never_commits_without_explicit_commit_flag(self):
        """Even dry_run=false+commit missing → dry_run stays True."""
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.post("/sepl/run", json={"dry_run": False})
            self.assertTrue(r.json()["dry_run"])

    def test_run_with_force_target_pinned_returns_aborted(self):
        """Trying to evolve a pinned prompt surfaces ABORTED_PINNED."""
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.post(
                "/sepl/run",
                json={"target": "moderator", "dry_run": True, "commit": True},
            )
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["outcome"], "aborted_pinned")
            self.assertTrue(body["dry_run"])


class TestSelectPreview(unittest.TestCase):
    """/sepl/select/preview returns Select's would-be decision."""

    def setUp(self):
        self.client = TestClient(app)

    def test_select_preview_structure(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.get("/sepl/select/preview")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIn("target", body)
            self.assertIn("reason", body)
            self.assertIn("candidates", body)
            self.assertIsInstance(body["candidates"], list)


class TestKillSwitchEndpoints(unittest.TestCase):
    """POST /sepl/kill-switch/run and GET /sepl/kill-switch/preview."""

    def setUp(self):
        self.client = TestClient(app)

    def test_kill_switch_preview_when_disabled_is_blocked(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "0"}, clear=False):
            r = self.client.get("/sepl/kill-switch/preview")
            self.assertEqual(r.status_code, 503)

    def test_kill_switch_preview_returns_list(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.get("/sepl/kill-switch/preview")
            self.assertEqual(r.status_code, 200)
            self.assertIsInstance(r.json(), list)

    def test_kill_switch_run_requires_commit_flag(self):
        """Without ``commit=true``, the endpoint must stay dry."""
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.post("/sepl/kill-switch/run", json={"dry_run": False})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIsInstance(body, list)
            for report in body:
                self.assertTrue(report["dry_run"], f"must stay dry without commit=true: {report}")

    def test_kill_switch_run_single_target(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.post(
                "/sepl/kill-switch/run",
                json={"target": "bull", "dry_run": True},
            )
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(len(body), 1)
            self.assertEqual(body[0]["target_name"], "bull")

    def test_status_includes_rollback_tunables(self):
        with patch.dict(os.environ, {"SEPL_ENABLE": "1"}, clear=False):
            r = self.client.get("/sepl/status")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIn("rollback_tunables", body)
            self.assertIn("margin", body["rollback_tunables"])
            self.assertIn("min_samples", body["rollback_tunables"])
            self.assertIn("window_hours", body["rollback_tunables"])


if __name__ == "__main__":
    unittest.main()
