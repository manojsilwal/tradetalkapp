import unittest
import json
from typing import Any, Dict, List
from backend.knowledge_store import KnowledgeStore


class FakeCollection:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def add(self, documents, metadatas, ids):
        for doc, meta, _id in zip(documents, metadatas, ids):
            self.rows.append({"doc": doc, "meta": meta, "id": _id})

    def count(self) -> int:
        return len(self.rows)

    def query(self, query_texts, n_results, where=None):
        q = query_texts[0]
        q_words = set(q.lower().replace(":", "").replace(",", "").split())
        words = q.lower().split()
        ticker = words[-2] if len(words) >= 3 else ""
        
        matched = []
        for r in self.rows:
            doc_words = set(r["doc"].lower().replace(":", "").replace(",", "").split())
            intersection = q_words.intersection(doc_words)
            if intersection:
                similarity = len(intersection)
                if ticker and ticker in doc_words:
                    similarity += 10
                matched.append((r, similarity))
        
        matched_sorted = sorted(
            matched,
            key=lambda x: (x[1], float(x[0]["meta"].get("effectiveness_score", 0.5)), x[0]["meta"].get("date", "")),
            reverse=True
        )
        
        docs = [m[0]["doc"] for m in matched_sorted[:n_results]]
        metas = [m[0]["meta"] for m in matched_sorted[:n_results]]
        return {
            "documents": [docs],
            "metadatas": [metas],
        }


class TestSwarmReflectionFallback(unittest.TestCase):
    def setUp(self):
        self.collections = {}
        self.ks = KnowledgeStore.__new__(KnowledgeStore)
        
        def fake_safe_col(name: str):
            return self.collections.setdefault(name, FakeCollection())
            
        self.ks._safe_col = fake_safe_col

    def test_query_swarm_reflections_deduplication_and_fallback(self):
        col = self.ks._safe_col("swarm_reflections")
        
        col.add(
            documents=["Swarm reflection GME BULL_NORMAL: Lesson A", "Swarm reflection GME BULL_NORMAL: Lesson A"],
            metadatas=[{"effectiveness_score": 0.8, "date": "2026-06-01"}, {"effectiveness_score": 0.8, "date": "2026-06-01"}],
            ids=["id1", "id2"]
        )
        col.add(
            documents=["Swarm reflection Short Interest BULL_NORMAL: Lesson B", "Swarm reflection Short Interest BULL_NORMAL: Lesson C"],
            metadatas=[{"effectiveness_score": 0.9, "date": "2026-06-02"}, {"effectiveness_score": 0.7, "date": "2026-06-02"}],
            ids=["id3", "id4"]
        )
        
        results = self.ks.query_swarm_reflections("Short Interest GME BULL_NORMAL", n_results=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "Swarm reflection GME BULL_NORMAL: Lesson A")
        
        results = self.ks.query_swarm_reflections("Short Interest GME BULL_NORMAL", n_results=2)
        self.assertEqual(len(results), 2)
        self.assertIn("Swarm reflection GME BULL_NORMAL: Lesson A", results)
        self.assertIn("Swarm reflection Short Interest BULL_NORMAL: Lesson B", results)


if __name__ == "__main__":
    unittest.main()
