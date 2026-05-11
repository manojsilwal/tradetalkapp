import unittest

from backend.predictor.eval.runner import run_replay_smoke


class TestEvalSmoke(unittest.TestCase):
    def test_replay_runs(self) -> None:
        out = run_replay_smoke(limit=3)
        self.assertTrue(out.get("ok"))
        self.assertGreaterEqual(out.get("ok_count", 0), 1)


if __name__ == "__main__":
    unittest.main()
