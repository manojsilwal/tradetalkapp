"""Tests for progress.db path resolution."""
import os
import tempfile
import unittest

from backend import progress_db as pdb


class TestProgressDbPath(unittest.TestCase):
    def tearDown(self):
        for key in ("PROGRESS_DB_PATH", "TRADETALK_DATA_DIR"):
            os.environ.pop(key, None)

    def test_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "nested", "progress.db")
            os.environ["PROGRESS_DB_PATH"] = target
            self.assertEqual(pdb.resolve_progress_db_path(), target)
            self.assertTrue(os.path.isdir(os.path.dirname(target)))

    def test_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADETALK_DATA_DIR"] = tmp
            self.assertEqual(
                pdb.resolve_progress_db_path(),
                os.path.join(tmp, "progress.db"),
            )


if __name__ == "__main__":
    unittest.main()
