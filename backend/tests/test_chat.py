"""Chat API and RAG rerank smoke tests (no live LLM stream)."""
import unittest

from fastapi.testclient import TestClient

from backend.main import app
from backend import chat_service


class TestChatRerank(unittest.TestCase):
    def test_rerank_orders_by_score(self):
        hits = [
            {"document": "old", "metadata": {"date": "2020-01-01"}, "distance": 0.1},
            {"document": "new", "metadata": {"date": "2025-01-01"}, "distance": 0.15},
        ]
        out = chat_service.rerank_hits(hits)
        self.assertEqual(out[0]["document"], "new")


class TestChatRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_bootstrap(self):
        r = self.client.get("/chat/bootstrap")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("l1_updated_at", data)
        self.assertIn("l1", data)

    def test_session_open(self):
        r = self.client.post("/chat/session")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("session_id", data)
        self.assertIn("assembled_at", data)


if __name__ == "__main__":
    unittest.main()
