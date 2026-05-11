import json
import os
import unittest


class TestReplayCorpus(unittest.TestCase):
    def test_file_has_fifty_tuples(self) -> None:
        path = os.path.join(
            os.path.dirname(__file__), "..", "predictor", "replay_corpus.json"
        )
        path = os.path.abspath(path)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data), 50)
        for row in data:
            self.assertIn("ticker", row)
            self.assertIn("as_of", row)


if __name__ == "__main__":
    unittest.main()
