"""Unit tests for OpenRouter API key collection and round-robin pool."""
import os
import threading
import unittest

from backend.openrouter_pool import (
    OpenRouterClientPool,
    collect_openrouter_api_keys,
    get_or_create_openrouter_pool,
    should_try_other_openrouter_keys_on_429,
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

    def test_next_async_returns_async_half_of_round_robin_pair(self):
        pool = OpenRouterClientPool.__new__(OpenRouterClientPool)
        pool._pairs = [
            (_FakeSync("a"), _FakeAsync("a-async")),
            (_FakeSync("b"), _FakeAsync("b-async")),
        ]
        pool._lock = threading.Lock()
        pool._idx = 0
        self.assertIs(pool.next_async(), pool._pairs[0][1])
        self.assertIs(pool.next_async(), pool._pairs[1][1])

    def test_sync_clients_for_request_strict_round_robin_one_client(self):
        pool = OpenRouterClientPool.__new__(OpenRouterClientPool)
        pool._pairs = [
            (_FakeSync("a"), _FakeAsync("a")),
            (_FakeSync("b"), _FakeAsync("b")),
        ]
        pool._lock = threading.Lock()
        pool._idx = 0
        c0 = pool.sync_clients_for_request(False)
        c1 = pool.sync_clients_for_request(False)
        self.assertEqual(len(c0), 1)
        self.assertEqual(len(c1), 1)
        self.assertEqual(c0[0].name, "a")
        self.assertEqual(c1[0].name, "b")

    def test_sync_clients_for_request_failover_matches_two_clients(self):
        pool = OpenRouterClientPool.__new__(OpenRouterClientPool)
        pool._pairs = [
            (_FakeSync("a"), _FakeAsync("a")),
            (_FakeSync("b"), _FakeAsync("b")),
        ]
        pool._lock = threading.Lock()
        pool._idx = 0
        full = pool.sync_clients_for_request(True)
        self.assertEqual(len(full), 2)
        self.assertEqual(full[0].name, "a")
        self.assertEqual(full[1].name, "b")


class TestShouldTryOtherKeys(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("OPENROUTER_429_TRY_OTHER_KEYS", None)

    def test_default_false(self):
        os.environ.pop("OPENROUTER_429_TRY_OTHER_KEYS", None)
        self.assertFalse(should_try_other_openrouter_keys_on_429())

    def test_truthy(self):
        os.environ["OPENROUTER_429_TRY_OTHER_KEYS"] = "1"
        self.assertTrue(should_try_other_openrouter_keys_on_429())


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
