import unittest
from datetime import date

from backend.predictor.leakage_guard import LeakageError, assert_available_before_as_of


class TestLeakageGuard(unittest.TestCase):
    def test_allows_same_day(self) -> None:
        assert_available_before_as_of(
            observed_at=date(2020, 6, 1),
            available_at=date(2020, 6, 1),
        )

    def test_rejects_lookahead(self) -> None:
        with self.assertRaises(LeakageError):
            assert_available_before_as_of(
                observed_at=date(2020, 6, 1),
                available_at=date(2020, 7, 1),
            )


if __name__ == "__main__":
    unittest.main()
