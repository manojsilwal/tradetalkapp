"""Tests for bounded capacity connector cache eviction and cleanup."""
import time
import unittest

from backend import connector_cache as cc


class TestConnectorCacheEviction(unittest.TestCase):
    def setUp(self):
        # Clear the internal store before each test
        with cc._lock:
            cc._store.clear()

    def tearDown(self):
        # Clean up after tests
        with cc._lock:
            cc._store.clear()

    def test_basic_caching(self):
        cc.set_cached("test_conn", {"data": 123}, "AAPL")
        val = cc.get_cached("test_conn", "AAPL")
        self.assertEqual(val, {"data": 123})

    def test_capacity_eviction_expired(self):
        # Set a bunch of items with expired timestamps manually
        now = time.time()
        with cc._lock:
            for i in range(1000):
                cc._store[f"test_conn::TICKER{i}"] = (now - 1000, f"val_{i}")
        
        # Adding one more item should trigger cleanup and evict all expired entries
        cc.set_cached("test_conn", "new_val", "AAPL")
        
        # Verify expired items were cleaned up, and the new one remains
        self.assertEqual(len(cc._store), 1)
        self.assertEqual(cc.get_cached("test_conn", "AAPL"), "new_val")

    def test_capacity_eviction_oldest(self):
        # Fill cache with fresh items (not expired)
        now = time.time()
        with cc._lock:
            for i in range(1000):
                # Set times in increasing order so oldest are first
                cc._store[f"test_conn::TICKER{i}"] = (now + i, f"val_{i}")
        
        self.assertEqual(len(cc._store), 1000)
        
        # Adding one more item should trigger cleanup. Since none are expired,
        # it should evict the oldest 100 entries.
        cc.set_cached("test_conn", "new_val", "AAPL")
        
        # Cache size should be 1000 - 100 + 1 = 901
        self.assertEqual(len(cc._store), 901)
        
        # Oldest entries (e.g. TICKER0 through TICKER99) should be gone
        with cc._lock:
            for i in range(100):
                self.assertNotIn(f"test_conn::TICKER{i}", cc._store)
            # TICKER100 should still be there
            self.assertIn(f"test_conn::TICKER100", cc._store)
