"""Offline tests for the 5-year submissions fetcher (recent + paginated files)."""
import asyncio
import unittest
from datetime import datetime, timedelta

from backend.coral_skills import sec_13f_ingestion as ing


def _d(days_ago: int) -> str:
    return (datetime.utcnow().date() - timedelta(days=days_ago)).isoformat()


class SubmissionsPaginationTest(unittest.TestCase):
    def setUp(self):
        self._orig = ing.edgar.get_json

    def tearDown(self):
        ing.edgar.get_json = self._orig

    def _install_fake(self, root, files):
        async def fake_get_json(url):
            if url.endswith("/older.json"):
                return files["older.json"]
            return root
        ing.edgar.get_json = fake_get_json

    def test_flattens_recent_and_files_and_filters(self):
        root = {
            "name": "Test Manager",
            "filings": {
                "recent": {
                    "form": ["13F-HR", "10-K"],
                    "accessionNumber": ["acc-recent", "acc-10k"],
                    "filingDate": [_d(60), _d(70)],
                    "reportDate": [_d(90), _d(100)],
                    "primaryDocument": ["primary.html", "x.html"],
                },
                "files": [{"name": "older.json"}],
            },
        }
        older = {
            "form": ["13F-HR", "13F-HR"],
            "accessionNumber": ["acc-older", "acc-ancient"],
            "filingDate": [_d(400), _d(3000)],  # second is > 5y -> filtered out
            "reportDate": [_d(430), _d(3030)],
            "primaryDocument": ["p1.html", "p2.html"],
        }
        self._install_fake(root, {"older.json": older})

        result = asyncio.run(ing.fetch_submissions_5y("1234567890"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["entity_name"], "Test Manager")
        accs = [f["accession_number"] for f in result["filings"]]
        self.assertIn("acc-recent", accs)
        self.assertIn("acc-older", accs)
        self.assertNotIn("acc-10k", accs)       # wrong form
        self.assertNotIn("acc-ancient", accs)   # outside 5y window
        # Sorted most-recent period first.
        self.assertEqual(result["filings"][0]["accession_number"], "acc-recent")

    def test_amendment_supersedes_original_for_same_period(self):
        period = _d(90)
        root = {
            "name": "Amend Co",
            "filings": {
                "recent": {
                    "form": ["13F-HR", "13F-HR/A"],
                    "accessionNumber": ["acc-orig", "acc-amend"],
                    "filingDate": [_d(80), _d(40)],  # amendment filed later
                    "reportDate": [period, period],
                    "primaryDocument": ["a.html", "b.html"],
                },
                "files": [],
            },
        }
        self._install_fake(root, {})
        result = asyncio.run(ing.fetch_submissions_5y("1234567890"))
        self.assertEqual(len(result["filings"]), 1)
        self.assertEqual(result["filings"][0]["accession_number"], "acc-amend")
        self.assertEqual(result["filings"][0]["form_type"], "13F-HR/A")


if __name__ == "__main__":
    unittest.main()
