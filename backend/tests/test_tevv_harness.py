"""Phase E — TEVV case bank integration (runner exit 0)."""
import unittest
from pathlib import Path

from backend.eval.tevv_runner import CASE_BANK_PATH, run_all


class TestTevvHarness(unittest.TestCase):
    def test_case_bank_exists(self):
        self.assertTrue(CASE_BANK_PATH.is_file(), f"missing {CASE_BANK_PATH}")

    def test_all_cases_pass_or_skip_reasoning_stub(self):
        _results, summary = run_all()
        self.assertEqual(
            summary["failed_count"],
            0,
            msg=f"TEVV failures: {summary.get('failures')}",
        )
        self.assertGreaterEqual(summary["passed"], 1)
        # 20 cases: one reasoning_quality stub is skipped
        self.assertEqual(summary["total_cases"], 20)


if __name__ == "__main__":
    unittest.main()
