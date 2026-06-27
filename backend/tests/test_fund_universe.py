"""Offline tests for the fund leaderboard universe builder."""
import asyncio
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend import fund_leaderboard_universe as uni


def _write_zip(path: Path):
    submission = "\n".join([
        "ACCESSION_NUMBER\tCIK\tSUBMISSIONTYPE\tPERIODOFREPORT",
        "acc-big\t1067983\t13F-HR\t31-DEC-2024",
        "acc-small\t1336528\t13F-HR\t31-DEC-2024",
        "acc-old\t1067983\t13F-HR\t30-SEP-2024",  # older period, excluded
    ])
    coverpage = "\n".join([
        "ACCESSION_NUMBER\tFILINGMANAGER_NAME",
        "acc-big\tBerkshire Hathaway",
        "acc-small\tPershing Square",
        "acc-old\tBerkshire Hathaway",
    ])
    infotable = "\n".join([
        "ACCESSION_NUMBER\tVALUE",
        "acc-big\t900000000",
        "acc-big\t100000000",
        "acc-small\t5000000",
        "acc-old\t1\t",  # excluded period
    ])
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("2024q4_form13f/SUBMISSION.tsv", submission)
        zf.writestr("2024q4_form13f/COVERPAGE.tsv", coverpage)
        zf.writestr("2024q4_form13f/INFOTABLE.tsv", infotable)


class BulkParseTest(unittest.TestCase):
    def test_parse_bulk_zip_ranks_by_value_for_latest_period(self):
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / "form13f.zip"
            _write_zip(zp)
            rows = uni._parse_bulk_zip(zp)
        self.assertEqual(len(rows), 2)  # acc-old period excluded -> only latest period CIKs
        self.assertEqual(rows[0]["cik"], "1067983")
        self.assertEqual(rows[0]["name"], "Berkshire Hathaway")
        self.assertAlmostEqual(rows[0]["value"], 1_000_000_000.0)
        self.assertEqual(rows[1]["cik"], "1336528")
        self.assertAlmostEqual(rows[1]["value"], 5_000_000.0)


class CuratedYamlIntegrityTest(unittest.TestCase):
    """Guards the shipped curated universe against the wrong-CIK class of bug."""

    def _managers(self):
        import yaml
        path = os.path.join(os.path.dirname(uni.__file__), "data", "fund_universe.yml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("managers", [])

    def test_no_duplicate_ciks(self):
        ciks = [str(m.get("cik")).strip() for m in self._managers()]
        dupes = {c for c in ciks if ciks.count(c) > 1}
        self.assertEqual(dupes, set(), f"duplicate CIKs in fund_universe.yml: {dupes}")

    def test_every_manager_has_cik_name_and_philosophy(self):
        for m in self._managers():
            self.assertTrue(str(m.get("cik", "")).strip().isdigit(), f"bad cik: {m}")
            self.assertTrue((m.get("name") or "").strip(), f"missing name: {m}")
            self.assertTrue((m.get("philosophy") or "").strip(), f"missing philosophy: {m.get('name')}")

    def test_known_filers_have_correct_ciks(self):
        by_name = {m.get("name"): str(m.get("cik")).strip() for m in self._managers()}
        # Regression guard for the CIK fixes (Lone Pine/Viking/Elliott/Geode) and
        # the curated additions (Situational Awareness/TCI/Akre/Pabrai).
        expected = {
            "Lone Pine Capital": "1061165",
            "Viking Global Investors": "1103804",
            "Elliott Investment Management": "1791786",
            "Geode Capital Management": "1214717",
            "Pershing Square Capital Management": "1336528",
            "TCI Fund Management": "1647251",
            "Akre Capital Management": "1112520",
            "Situational Awareness LP": "2045724",
        }
        for name, cik in expected.items():
            self.assertEqual(by_name.get(name), cik, f"{name} should have CIK {cik}")

    def test_philosophy_flows_through_universe_rows(self):
        rows = uni._universe_from_yaml(
            os.path.join(os.path.dirname(uni.__file__), "data", "fund_universe.yml"),
            uni.RANKING_EXTERNAL_AUM, "fund_universe.yml", top_n=100,
        )
        self.assertTrue(all("philosophy" in r for r in rows))
        self.assertTrue(any(r.get("philosophy") for r in rows))


class CuratedYamlTest(unittest.TestCase):
    def test_yaml_curated_mode_ranks_by_external_aum(self):
        yaml_text = (
            "managers:\n"
            "  - cik: 111\n    name: Small Co\n    external_aum_usd: 1000\n"
            "  - cik: 222\n    name: Big Co\n    external_aum_usd: 9000\n"
            "  - cik: 222\n    name: Big Co Dup\n    external_aum_usd: 9000\n"  # dedup by cik
        )
        with tempfile.TemporaryDirectory() as td:
            yp = os.path.join(td, "u.yml")
            with open(yp, "w") as f:
                f.write(yaml_text)
            rows = uni._universe_from_yaml(yp, uni.RANKING_EXTERNAL_AUM, "u.yml", top_n=10)
        self.assertEqual(len(rows), 2)  # deduped
        self.assertEqual(rows[0]["name"], "Big Co")
        self.assertEqual(rows[0]["ranking_method"], uni.RANKING_EXTERNAL_AUM)

    def test_build_universe_curated_without_persist(self):
        rows = asyncio.run(
            uni.build_universe(ranking_mode=uni.RANKING_EXTERNAL_AUM, top_n=5, persist=False)
        )
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["ranking_method"] == uni.RANKING_EXTERNAL_AUM for r in rows))


if __name__ == "__main__":
    unittest.main()
