"""Unit tests for ChromaVectorBackend embedding dimension mismatch auto-healing."""
import os
import shutil
import tempfile
import unittest
from backend.vector_backends import ChromaVectorBackend


class FakeEmbeddingFunction:
    def __init__(self, dimension: int):
        self.dimension = dimension

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [[1.0] * self.dimension for _ in input]


class TestChromaDimensionAutoHeal(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for persistent Chroma testing
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_auto_heal_dimension_mismatch_on_add(self):
        # 1. Initialize backend with a persistent client
        backend = ChromaVectorBackend(chroma_path=self.tmp_dir)
        
        # 2. Configure with a 3-dimensional embedding function
        embed_fn_3d = FakeEmbeddingFunction(3)
        backend._embedding_function = embed_fn_3d
        backend.ensure_collection("test_heal_col")
        
        # 3. Add document with 3-dimensional embeddings (creates collection schema at 3D)
        backend.add(
            collection="test_heal_col",
            documents=["doc1"],
            metadatas=[{"meta": "val"}],
            ids=["id1"],
            embeddings=[[1.0, 1.0, 1.0]]
        )
        self.assertEqual(backend.count("test_heal_col"), 1)
        
        # 4. Now attempt to add a 5-dimensional embedding to the 3-dimensional collection.
        # This will raise a dimension mismatch, triggering the auto-heal deletion & recreation.
        backend.add(
            collection="test_heal_col",
            documents=["doc2"],
            metadatas=[{"meta": "val2"}],
            ids=["id2"],
            embeddings=[[1.0, 1.0, 1.0, 1.0, 1.0]]
        )
        
        # 5. Verify that it was successfully added (the collection was healed/recreated)
        # Note: Since the old collection was deleted to heal, it should contain only the new doc.
        self.assertEqual(backend.count("test_heal_col"), 1)

    def test_auto_heal_dimension_mismatch_on_query(self):
        # 1. Initialize first backend client and write a 3D document
        backend1 = ChromaVectorBackend(chroma_path=self.tmp_dir)
        backend1._embedding_function = FakeEmbeddingFunction(3)
        backend1.ensure_collection("test_heal_query")
        backend1.add(
            collection="test_heal_query",
            documents=["doc1"],
            metadatas=[{"meta": "val"}],
            ids=["id1"],
            embeddings=[[1.0, 1.0, 1.0]]
        )
        self.assertEqual(backend1.count("test_heal_query"), 1)
        
        # 2. Simulate server restart with a new client instance and a 5D embedding function
        # This client points to the same directory on disk
        backend2 = ChromaVectorBackend(chroma_path=self.tmp_dir)
        backend2._embedding_function = FakeEmbeddingFunction(5)
        backend2.ensure_collection("test_heal_query")
        
        # 3. Querying now will generate 5D embeddings but the database on disk has a 3D index.
        # This will raise a dimension mismatch error, trigger the auto-heal deletion & recreation of an empty collection.
        results = backend2.query(
            collection="test_heal_query",
            query_text="query",
            n_results=1
        )
        # It should auto-heal, return empty results (since collection is now empty), and not crash.
        self.assertEqual(len(results["documents"]), 0)
        self.assertEqual(backend2.count("test_heal_query"), 0)


if __name__ == "__main__":
    unittest.main()
