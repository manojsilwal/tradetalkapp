"""Unit tests for batch ETL chunking (no network)."""
import os
import unittest
from unittest.mock import MagicMock, patch

from backend.batch_etl.pipeline import (
    _DEFAULT_OPENROUTER_EMBEDDING_MODEL,
    chunk_text,
    run_batch_etl,
)


class TestBatchEtlChunking(unittest.TestCase):
    def test_chunk_text_empty(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   "), [])

    def test_chunk_text_short(self):
        self.assertEqual(chunk_text("hello"), ["hello"])

    def test_chunk_text_overlap(self):
        s = "a" * 100 + "b" * 100
        parts = chunk_text(s, size=80, overlap=20)
        self.assertGreaterEqual(len(parts), 2)
        joined = "".join(parts)
        self.assertIn("a", joined)
        self.assertIn("b", joined)

    def test_default_openrouter_embedding_model_when_unset(self):
        """Supabase upsert no longer fails when OPENROUTER_EMBEDDING_MODEL secret is missing."""
        base = {
            "SUPABASE_URL": "https://x.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key",
            "OPENROUTER_API_KEY": "or-key",
        }
        with patch.dict(os.environ, base, clear=False):
            os.environ.pop("OPENROUTER_EMBEDDING_MODEL", None)
            with patch(
                "backend.batch_etl.pipeline._yfinance_profile_blob",
                return_value=("word " * 200).strip(),
            ):
                mock_cls = MagicMock()
                inst = MagicMock()
                mock_cls.return_value = inst
                with patch("backend.vector_backends.SupabaseVectorBackend", mock_cls):
                    result = run_batch_etl(["SPY"], upload_hf=False, upsert_supabase=True)
            self.assertTrue(result.get("ok"), msg=result)
            self.assertEqual(
                os.environ.get("OPENROUTER_EMBEDDING_MODEL"),
                _DEFAULT_OPENROUTER_EMBEDDING_MODEL,
            )
            mock_cls.assert_called_once_with("https://x.supabase.co", "test-key")
            inst.add.assert_called_once()


if __name__ == "__main__":
    unittest.main()
