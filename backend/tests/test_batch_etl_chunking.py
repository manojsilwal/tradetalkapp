"""Unit tests for batch ETL chunking (no network)."""
import unittest

from backend.batch_etl.pipeline import chunk_text


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


if __name__ == "__main__":
    unittest.main()
