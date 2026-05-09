"""Tests for VECTOR_EMBEDDING_PROVIDER / Google vs OpenRouter embedding selection."""
import os
import unittest
from unittest.mock import patch


class TestGoogleEmbeddingsFlag(unittest.TestCase):
    def test_prefers_google_when_key_present(self):
        from backend.vector_backends import google_embeddings_enabled

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "x",
                "VECTOR_EMBEDDING_PROVIDER": "",
            },
            clear=False,
        ):
            self.assertTrue(google_embeddings_enabled())

    def test_openrouter_forces_off_google(self):
        from backend.vector_backends import google_embeddings_enabled

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "x",
                "VECTOR_EMBEDDING_PROVIDER": "openrouter",
            },
            clear=False,
        ):
            self.assertFalse(google_embeddings_enabled())

    def test_no_key_means_false(self):
        from backend.vector_backends import google_embeddings_enabled

        removed = {}
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if k in os.environ:
                removed[k] = os.environ.pop(k)
        try:
            self.assertFalse(google_embeddings_enabled())
        finally:
            os.environ.update(removed)


if __name__ == "__main__":
    unittest.main()
