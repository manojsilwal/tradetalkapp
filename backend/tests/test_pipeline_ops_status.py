"""Pipeline Ops sections degrade gracefully offline (no GCP creds/libs)."""
import unittest


class TestPipelineOpsSections(unittest.TestCase):
    def setUp(self):
        from backend.routers import pipeline_ops
        self.po = pipeline_ops

    def test_cloud_run_section_has_available_flag(self):
        out = self.po._cloud_run_jobs()
        self.assertIn("available", out)
        self.assertIsInstance(out["available"], bool)
        if not out["available"]:
            self.assertIn("reason", out)

    def test_scheduler_section_has_available_flag(self):
        out = self.po._scheduler()
        self.assertIn("available", out)
        self.assertIsInstance(out["available"], bool)

    def test_brain_section_never_raises(self):
        out = self.po._brain_freshness()
        self.assertIn("available", out)

    def test_bigquery_section_never_raises(self):
        out = self.po._bigquery_freshness()
        self.assertIn("available", out)


if __name__ == "__main__":
    unittest.main()
