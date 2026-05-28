"""FastAPI route tests for /macro/supply-chain/* endpoints."""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class TestSupplyChainRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = self._tmp.name
        os.environ["SUPPLY_CHAIN_DB_PATH"] = self.db

        from backend.supply_chain.seed_chains import seed_supply_chain_db
        seed_supply_chain_db(self.db)

        from backend.main import app
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("SUPPLY_CHAIN_DB_PATH", None)
        os.unlink(self.db)

    def test_graph_all(self):
        r = self.client.get("/macro/supply-chain/graph")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertGreater(len(data["nodes"]), 0)

    def test_graph_with_year(self):
        r = self.client.get("/macro/supply-chain/graph?year=2025")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["year"], 2025)

    def test_graph_with_root(self):
        r = self.client.get("/macro/supply-chain/graph?year=2025&root=LLY")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        node_ids = {n["node_id"] for n in data["nodes"]}
        self.assertIn("LLY", node_ids)
        self.assertIn("OPENAI", node_ids)

    def test_node_detail(self):
        r = self.client.get("/macro/supply-chain/nodes/NVDA")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["node"]["node_id"], "NVDA")
        self.assertGreater(len(data["upstream"]) + len(data["downstream"]), 0)

    def test_node_detail_404(self):
        r = self.client.get("/macro/supply-chain/nodes/DOESNOTEXIST")
        self.assertEqual(r.status_code, 404)

    def test_timeline(self):
        r = self.client.get("/macro/supply-chain/timeline?from=2023&to=2025")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("snapshots", data)
        self.assertGreater(len(data["snapshots"]), 0)

    def test_sector_sankey(self):
        r = self.client.get("/macro/supply-chain/sector-sankey?year=2025")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["year"], 2025)
        self.assertGreater(len(data["links"]), 0)

    def test_sector_sankey_timeline(self):
        r = self.client.get("/macro/supply-chain/sector-sankey/timeline?from=2023&to=2025")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("snapshots", data)
        self.assertGreater(len(data["snapshots"]), 0)


if __name__ == "__main__":
    unittest.main()
