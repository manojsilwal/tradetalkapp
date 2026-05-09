"""Smoke route wiring — no live NVIDIA/Google calls (routes gated)."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app


class TestProviderSmokeRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_smoke_routes_404_when_disabled(self):
        # Default CI/local: probes must not be reachable without explicit opt-in.
        with patch.dict(os.environ, {"ALLOW_PROVIDER_SMOKE": ""}, clear=False):
            r = self.client.get("/health/smoke/status")
            self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
