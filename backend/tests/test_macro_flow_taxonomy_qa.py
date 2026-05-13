import unittest

import pandas as pd

from backend.macro_flow.flow_qa_verifier import verify_flow_qa
from backend.macro_flow.macro_flow_agent import top_movers_for_category
from backend.macro_flow.taxonomy.seed_taxonomy import validate_taxonomy


class TestMacroFlowTaxonomyAndQa(unittest.TestCase):
    def test_validate_taxonomy(self):
        validate_taxonomy()

    def test_qa_durable(self):
        r = verify_flow_qa(flow_score=0.2, weighted_qual=0.7, fundamental_band="strong")
        self.assertEqual(r["qa_verdict"], "durable")

    def test_qa_speculative(self):
        r = verify_flow_qa(flow_score=0.2, weighted_qual=0.4, fundamental_band="neutral")
        self.assertEqual(r["qa_verdict"], "speculative")

    def test_top_movers_orders_by_abs_move(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        a = pd.DataFrame(
            {
                "Open": [10, 10, 10, 10, 10],
                "High": [11, 11, 11, 11, 11],
                "Low": [9, 9, 9, 9, 9],
                "Close": [10, 10, 10, 10, 20],
                "Volume": [1e6] * 5,
            },
            index=idx,
        )
        b = pd.DataFrame(
            {
                "Open": [100] * 5,
                "High": [101] * 5,
                "Low": [99] * 5,
                "Close": [100, 100, 100, 100, 101],
                "Volume": [1e6] * 5,
            },
            index=idx,
        )
        frames = {"AAA": a, "BBB": b}
        weights = [("AAA", 0.5), ("BBB", 0.5)]
        movers = top_movers_for_category(weights, frames, k=2)
        self.assertEqual(movers[0]["symbol"], "AAA")
        self.assertGreater(movers[0]["period_change_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
