import unittest

from backend.swarm_reliability.schema_discovery import (
    list_data_sources,
    list_fields,
    sample_records,
)


class _FakeKS:
    def query_with_metadata(self, collection, query_text, n_results=3):
        if collection == "macro_snapshots":
            return [
                {
                    "id": "m1",
                    "distance": 0.1,
                    "metadata": {"source": "fred", "date": "2026-05-08", "ticker": "SPY"},
                    "document": "Macro doc 1",
                },
                {
                    "id": "m2",
                    "distance": 0.2,
                    "metadata": {"source": "fred", "run_date": "2026-05-07"},
                    "document": "Macro doc 2",
                },
            ][:n_results]
        return []


class TestSchemaDiscovery(unittest.TestCase):
    def test_list_data_sources_non_empty(self):
        self.assertIn("macro_snapshots", list_data_sources())

    def test_list_fields_from_sampled_metadata(self):
        ks = _FakeKS()
        fields = list_fields(ks, "macro_snapshots")
        self.assertIn("source", fields)
        self.assertIn("date", fields)

    def test_sample_records_compact_shape(self):
        ks = _FakeKS()
        rows = sample_records(ks, "macro_snapshots", limit=1)
        self.assertEqual(len(rows), 1)
        self.assertIn("metadata", rows[0])
        self.assertIn("document", rows[0])
        self.assertLessEqual(len(rows[0]["document"]), 300)


if __name__ == "__main__":
    unittest.main()

