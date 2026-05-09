"""Phase E + Phase B — TEVV case bank integration (runner exit 0)."""
import unittest

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
        # 20 base + 7 anti-shortcut = 27 cases (one reasoning stub skipped).
        self.assertEqual(summary["total_cases"], 27)

    def test_shortcut_resistance_axis_runs(self):
        _results, summary = run_all()
        axes = summary["axes"]
        self.assertIn("shortcut_resistance", axes)
        self.assertGreaterEqual(axes["shortcut_resistance"]["total"], 7)
        self.assertEqual(axes["shortcut_resistance"]["failed"], 0)
        self.assertGreaterEqual(axes["shortcut_resistance"]["passed"], 7)


if __name__ == "__main__":
    unittest.main()
