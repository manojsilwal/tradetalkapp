"""Offline tests for S&P 500 stock-level macro flow graph."""
import os
import unittest
from unittest.mock import patch


class TestMacroStockGraph(unittest.TestCase):
  def setUp(self):
    self._env = patch.dict(
      os.environ,
      {
        "MACRO_STOCK_GRAPH_OFFLINE": "1",
        "MACRO_STOCK_GRAPH_MAX_TICKERS": "40",
        "MACRO_STOCK_GRAPH_TOP_K": "2",
      },
    )
    self._env.start()
    from backend.macro_flow.stock_graph import clear_stock_graph_cache

    clear_stock_graph_cache()

  def tearDown(self):
    self._env.stop()

  def test_build_offline_graph(self):
    from backend.macro_flow.stock_graph import build_stock_flow_graph

    payload = build_stock_flow_graph("1w")
    self.assertGreaterEqual(payload["node_count"], 40)
    self.assertGreater(payload["edge_count"], 0)
    self.assertTrue(payload["nodes"][0].get("ticker"))
    self.assertIn("flow_score", payload["nodes"][0])

  def test_bidirectional_edges_possible(self):
    from backend.macro_flow.stock_graph import build_stock_flow_graph

    payload = build_stock_flow_graph("1m")
    kinds = {e.get("bidirectional") for e in payload.get("edges") or []}
    self.assertTrue(True in kinds or False in kinds)


if __name__ == "__main__":
  unittest.main()
