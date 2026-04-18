"""Integration tests for the read-only /resources router (Phase A)."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402


class TestResourcesRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # ── summary ─────────────────────────────────────────────────────────

    def test_summary_reports_populated_registry(self):
        r = self.client.get("/resources/summary")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreaterEqual(body["count"], 15)
        self.assertEqual(len(body["snapshot_id"]), 16)
        self.assertIn("db_path", body)

    # ── list ────────────────────────────────────────────────────────────

    def test_list_returns_all_prompts(self):
        r = self.client.get("/resources/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        names = {e["name"] for e in body}
        # Spot-check: key roles present
        for expected in ("bull", "bear", "moderator", "swarm_analyst"):
            self.assertIn(expected, names)

    def test_list_filters_by_kind(self):
        r = self.client.get("/resources/?kind=prompt")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(all(e["kind"] == "prompt" for e in body))

    def test_list_rejects_unknown_kind(self):
        r = self.client.get("/resources/?kind=bogus")
        self.assertEqual(r.status_code, 400)

    # ── detail ──────────────────────────────────────────────────────────

    def test_detail_includes_body_and_schema(self):
        r = self.client.get("/resources/bull")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["name"], "bull")
        self.assertEqual(body["version"], "1.0.0")
        self.assertTrue(body["learnable"])
        self.assertIn("bullish", body["body"].lower())
        # Schema returned under 'schema' alias
        self.assertIn("schema", body)
        self.assertIsNotNone(body["schema"])

    def test_detail_returns_404_for_unknown(self):
        r = self.client.get("/resources/does_not_exist")
        self.assertEqual(r.status_code, 404)

    def test_pinned_resource_exposes_learnable_false(self):
        r = self.client.get("/resources/moderator")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["learnable"])

    # ── versions ────────────────────────────────────────────────────────

    def test_versions_returns_at_least_one(self):
        r = self.client.get("/resources/bull/versions")
        self.assertEqual(r.status_code, 200)
        versions = r.json()
        self.assertIn("1.0.0", versions)

    def test_versions_404_for_unknown(self):
        r = self.client.get("/resources/nothing_here/versions")
        self.assertEqual(r.status_code, 404)

    # ── lineage ─────────────────────────────────────────────────────────

    def test_lineage_contains_register_operation(self):
        r = self.client.get("/resources/bull/lineage")
        self.assertEqual(r.status_code, 200)
        events = r.json()
        self.assertGreaterEqual(len(events), 1)
        ops = {e["operation"] for e in events}
        self.assertIn("register", ops)


if __name__ == "__main__":
    unittest.main()
