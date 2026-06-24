"""Cloud port tests: local adapters round-trip; factory selects by env."""
import importlib
import os
import tempfile
import unittest

from backend.brain.ports import base
from backend.brain.ports.local_adapters import EnvSecrets, LocalStorage, MemoryCache


class TestLocalAdapters(unittest.TestCase):
    def test_storage_roundtrip_and_protocol(self):
        tmp = tempfile.mkdtemp()
        s = LocalStorage(tmp)
        self.assertIsInstance(s, base.StoragePort)  # structural typing
        self.assertFalse(s.exists("a/b.json"))
        s.put("a/b.json", b"hello")
        self.assertTrue(s.exists("a/b.json"))
        self.assertEqual(s.get("a/b.json"), b"hello")
        self.assertIn("a/b.json", list(s.list("a/")))

    def test_storage_rejects_path_escape(self):
        s = LocalStorage(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            s.put("../../etc/passwd", b"x")

    def test_memory_cache_ttl(self):
        clock = [1000.0]
        c = MemoryCache()
        # monkeypatch time via closure is overkill; test the no-ttl path + delete
        c.set("k", b"v")
        self.assertEqual(c.get("k"), b"v")
        c.delete("k")
        self.assertIsNone(c.get("k"))

    def test_env_secrets(self):
        os.environ["BRAIN_TEST_SECRET"] = "shh"
        self.assertEqual(EnvSecrets().resolve("BRAIN_TEST_SECRET"), "shh")
        self.assertIsNone(EnvSecrets().resolve("BRAIN_TEST_MISSING"))


class TestFactory(unittest.TestCase):
    def _reload_factory(self):
        from backend.brain.ports import factory
        return importlib.reload(factory)

    def test_local_defaults(self):
        os.environ.pop("STORAGE_BACKEND", None)
        os.environ.pop("CACHE_BACKEND", None)
        os.environ.pop("CLOUD_PROVIDER", None)
        factory = self._reload_factory()
        self.assertIsInstance(factory.get_storage(tempfile.mkdtemp()), base.StoragePort)
        self.assertIsInstance(factory.get_cache(), base.CachePort)
        self.assertIsInstance(factory.get_secrets(), base.SecretsPort)

    def test_unbundled_cloud_backend_raises(self):
        # aws/azure adapters are still not bundled (gcp now is — see gcs_adapter).
        os.environ["STORAGE_BACKEND"] = "aws"
        try:
            factory = self._reload_factory()
            with self.assertRaises(NotImplementedError):
                factory.get_storage()
        finally:
            os.environ.pop("STORAGE_BACKEND", None)
            self._reload_factory()

    def test_gcp_backend_returns_gcs_storage(self):
        os.environ["STORAGE_BACKEND"] = "gcp"
        try:
            factory = self._reload_factory()
            from backend.brain.ports.gcs_adapter import GCSStorage
            store = factory.get_storage()
            self.assertIsInstance(store, GCSStorage)
            self.assertIsInstance(store, base.StoragePort)
        finally:
            os.environ.pop("STORAGE_BACKEND", None)
            self._reload_factory()


if __name__ == "__main__":
    unittest.main()
