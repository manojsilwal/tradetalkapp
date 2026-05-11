import unittest

from backend.swarm_reliability.schemas import stable_json_hash


class TestStableJsonHash(unittest.TestCase):
    def test_equivalent_keys(self) -> None:
        a = {"z": 1, "a": {"nested": [3, 2, 1]}}
        b = {"a": {"nested": [3, 2, 1]}, "z": 1}
        self.assertEqual(stable_json_hash(a), stable_json_hash(b))


if __name__ == "__main__":
    unittest.main()
