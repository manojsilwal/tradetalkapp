"""Tests for supply chain JSON seed → SQLite."""
import os
import tempfile
import unittest

from backend.supply_chain.db import init_supply_chain_db
from backend.supply_chain.seed_chains import seed_supply_chain_db, node_count
from backend.supply_chain.store import get_graph, list_all_nodes


class TestSupplyChainSeed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = self._tmp.name
        os.environ["SUPPLY_CHAIN_DB_PATH"] = self.db

    def tearDown(self):
        os.environ.pop("SUPPLY_CHAIN_DB_PATH", None)
        os.unlink(self.db)

    def test_seed_creates_nodes(self):
        seed_supply_chain_db(self.db)
        nodes = list_all_nodes(self.db)
        ids = {n["node_id"] for n in nodes}
        for required in ("LLY", "OPENAI", "MSFT", "NVDA", "TSM", "ASML"):
            self.assertIn(required, ids, f"{required} must be seeded")

    def test_seed_creates_edges(self):
        seed_supply_chain_db(self.db)
        g = get_graph(year=2025, db_path=self.db)
        self.assertGreater(len(g["edges"]), 0)

    def test_lly_chain_has_5_hops(self):
        """LLY → OPENAI → MSFT → NVDA → TSM → ASML (5 hops minimum)."""
        seed_supply_chain_db(self.db)
        g = get_graph(year=2025, root="LLY", db_path=self.db)
        node_ids = {n["node_id"] for n in g["nodes"]}
        for hop in ("LLY", "OPENAI", "MSFT", "NVDA", "TSM", "ASML"):
            self.assertIn(hop, node_ids, f"{hop} reachable from LLY BFS")

    def test_openai_is_private(self):
        seed_supply_chain_db(self.db)
        nodes = list_all_nodes(self.db)
        openai = next(n for n in nodes if n["node_id"] == "OPENAI")
        self.assertFalse(openai["is_public"])
        self.assertIsNone(openai["ticker"])

    def test_idempotent_reseed(self):
        seed_supply_chain_db(self.db)
        n1 = node_count(self.db)
        seed_supply_chain_db(self.db)
        n2 = node_count(self.db)
        self.assertEqual(n1, n2)

    def test_apple_foxconn_chain(self):
        seed_supply_chain_db(self.db)
        g = get_graph(year=2025, root="AAPL", db_path=self.db)
        node_ids = {n["node_id"] for n in g["nodes"]}
        self.assertIn("FOXCONN", node_ids)
        self.assertIn("TSM", node_ids)

    def test_tesla_catl_chain(self):
        seed_supply_chain_db(self.db)
        g = get_graph(year=2025, root="TSLA", db_path=self.db)
        node_ids = {n["node_id"] for n in g["nodes"]}
        self.assertIn("CATL", node_ids)
        self.assertIn("ALB", node_ids)


if __name__ == "__main__":
    unittest.main()
