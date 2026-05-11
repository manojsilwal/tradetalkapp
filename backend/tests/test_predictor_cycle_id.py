import unittest

from backend.predictor.agent import new_cycle_id


class TestCycleId(unittest.TestCase):
    def test_format_and_uniqueness(self) -> None:
        seen = set()
        for _ in range(500):
            cid = new_cycle_id("AAPL", ["1d", "5d"])
            self.assertTrue(cid.startswith("predictor-AAPL-"))
            seen.add(cid)
        self.assertEqual(len(seen), 500)


if __name__ == "__main__":
    unittest.main()
