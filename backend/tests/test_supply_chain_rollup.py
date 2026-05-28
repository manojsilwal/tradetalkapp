"""Tests for supply chain sector rollup and temporal queries."""
import os
import tempfile
import unittest

from backend.supply_chain.seed_chains import seed_supply_chain_db
from backend.supply_chain.sector_rollup import sector_sankey, sector_sankey_timeline
from backend.supply_chain.temporal import get_flows_for_year, get_flow_series, get_snapshots


class TestSectorRollup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = self._tmp.name
        os.environ["SUPPLY_CHAIN_DB_PATH"] = self.db
        seed_supply_chain_db(self.db)

    def tearDown(self):
        os.environ.pop("SUPPLY_CHAIN_DB_PATH", None)
        os.unlink(self.db)

    def test_sector_sankey_2025(self):
        result = sector_sankey(2025, db_path=self.db)
        self.assertEqual(result["year"], 2025)
        self.assertGreater(len(result["nodes"]), 0)
        self.assertGreater(len(result["links"]), 0)
        sectors = {n["id"] for n in result["nodes"]}
        self.assertIn("Healthcare", sectors)
        self.assertIn("Semiconductors", sectors)

    def test_healthcare_to_software_flow(self):
        """LLY → OpenAI means Healthcare → Software in sector rollup."""
        result = sector_sankey(2025, db_path=self.db)
        link = next(
            (l for l in result["links"] if l["source"] == "Healthcare" and l["target"] == "Software"),
            None,
        )
        self.assertIsNotNone(link, "Healthcare → Software link must exist")
        self.assertGreater(link["value"], 0)

    def test_sector_sankey_timeline(self):
        snaps = sector_sankey_timeline(2023, 2025, db_path=self.db)
        self.assertGreater(len(snaps), 0)
        years = [s["year"] for s in snaps]
        self.assertIn(2025, years)


class TestTemporal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = self._tmp.name
        os.environ["SUPPLY_CHAIN_DB_PATH"] = self.db
        seed_supply_chain_db(self.db)

    def tearDown(self):
        os.environ.pop("SUPPLY_CHAIN_DB_PATH", None)
        os.unlink(self.db)

    def test_flows_for_year(self):
        result = get_flows_for_year(2025, db_path=self.db)
        self.assertEqual(result["year"], 2025)
        self.assertGreater(len(result["edges"]), 0)

    def test_flow_series(self):
        series = get_flow_series("NVDA", "TSM", 2020, 2026, db_path=self.db)
        self.assertGreater(len(series), 0)
        amounts = [s["amount_est_usd"] for s in series]
        self.assertTrue(all(a > 0 for a in amounts))
        # Post-2023 AI spike: 2025 > 2020
        a2020 = next((s["amount_est_usd"] for s in series if s["year"] == 2020), 0)
        a2025 = next((s["amount_est_usd"] for s in series if s["year"] == 2025), 0)
        self.assertGreater(a2025, a2020)

    def test_snapshots(self):
        snaps = get_snapshots(2023, 2025, root="LLY", db_path=self.db)
        self.assertGreater(len(snaps), 0)
        for s in snaps:
            self.assertIn(s["year"], [2023, 2024, 2025])


if __name__ == "__main__":
    unittest.main()
