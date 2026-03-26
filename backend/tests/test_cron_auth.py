"""Tests for cron secret authentication."""
import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend.cron_auth import require_cron_secret, cron_secret_configured


class TestCronSecretConfigured(unittest.TestCase):
    """Test cron_secret_configured helper."""

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "my-secret"})
    def test_returns_secret_when_set(self):
        self.assertEqual(cron_secret_configured(), "my-secret")

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": ""})
    def test_returns_empty_when_blank(self):
        self.assertEqual(cron_secret_configured(), "")

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_empty_when_unset(self):
        result = cron_secret_configured()
        self.assertEqual(result, "")


class TestRequireCronSecret(unittest.TestCase):
    """Test the FastAPI dependency."""

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": ""})
    def test_no_secret_configured_passes(self):
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            require_cron_secret(authorization=None, x_cron_secret=None)
        )
        self.assertIsNone(result)

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "test-secret"})
    def test_correct_bearer_passes(self):
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            require_cron_secret(authorization="Bearer test-secret", x_cron_secret=None)
        )
        self.assertIsNone(result)

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "test-secret"})
    def test_correct_header_passes(self):
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            require_cron_secret(authorization=None, x_cron_secret="test-secret")
        )
        self.assertIsNone(result)

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "test-secret"})
    def test_wrong_secret_raises(self):
        import asyncio
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            asyncio.get_event_loop().run_until_complete(
                require_cron_secret(authorization="Bearer wrong", x_cron_secret=None)
            )
        self.assertEqual(ctx.exception.status_code, 401)

    @patch.dict(os.environ, {"PIPELINE_CRON_SECRET": "test-secret"})
    def test_missing_secret_raises(self):
        import asyncio
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            asyncio.get_event_loop().run_until_complete(
                require_cron_secret(authorization=None, x_cron_secret=None)
            )
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
