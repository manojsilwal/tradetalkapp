"""Unit tests for OpenRouter API key collection and round-robin pool."""
import os
import threading
import unittest

from backend.openrouter_pool import (
    OpenRouterClientPool,
    collect_openrouter_api_keys,
    get_or_create_openrouter_pool,
)


class TestCollectOpenrouterApiKeys(unittest.TestCase):
    def test_collect_primary_only(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-primary"
        os.environ.pop("OPENROUTER_API_KEY_2", None)
        try:
            keys = collect_openrouter_api_keys()
            self.assertEqual(keys, ["sk-primary"])
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)

    def test_collect_primary_and_secondary(self):
        os.environ["OPENROUTER_API_KEY"] = "k1"
        os.environ["OPENROUTER_API_KEY_2"] = "k2"
        try:
            keys = collect_openrouter_api_keys()
            self.assertEqual(keys, ["k1", "k2"])
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ.pop("OPENROUTER_API_KEY_2", None)

    def test_collect_skips_empty(self):
        os.environ["OPENROUTER_API_KEY"] = ""
        os.environ["OPENROUTER_API_KEY_2"] = "only2"
        try:
            keys = collect_openrouter_api_keys()
            self.assertEqual(keys, ["only2"])
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ.pop("OPENROUTER_API_KEY_2", None)


class _FakeSync:
    def __init__(self, name: str):
        self.name = name


class _FakeAsync:
    def __init__(self, name: str):
        self.name = name


class TestOpenRouterClientPool(unittest.TestCase):
    def test_round_robin_order(self):
        pool = OpenRouterClientPool.__new__(OpenRouterClientPool)
        pool._pairs = [
            (_FakeSync("a"), _FakeAsync("a")),
            (_FakeSync("b"), _FakeAsync("b")),
        ]
        pool._lock = threading.Lock()
        pool._idx = 0

        s0, _ = pool.next_pair()
        s1, _ = pool.next_pair()
        s2, _ = pool.next_pair()
        self.assertEqual(s0.name, "a")
        self.assertEqual(s1.name, "b")
        self.assertEqual(s2.name, "a")

    def test_next_sync_matches_next_pair_first_element(self):
        pool = OpenRouterClientPool.__new__(OpenRouterClientPool)
        pool._pairs = [(_FakeSync("x"), _FakeAsync("x"))]
        pool._lock = threading.Lock()
        pool._idx = 0
        self.assertIs(pool.next_sync(), pool.next_pair()[0])


class TestGetOrCreateSingleton(unittest.TestCase):
    def tearDown(self):
        import backend.openrouter_pool as m

        m._pool = None

    def test_singleton_returns_same_instance(self):
        import backend.openrouter_pool as m

        os.environ["OPENROUTER_API_KEY"] = "one"
        os.environ.pop("OPENROUTER_API_KEY_2", None)
        try:
            m._pool = None
            p1 = get_or_create_openrouter_pool("https://openrouter.ai/api/v1", {})
            p2 = get_or_create_openrouter_pool("https://openrouter.ai/api/v1", {})
            self.assertIs(p1, p2)
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
            m._pool = None


if __name__ == "__main__":
    unittest.main()
