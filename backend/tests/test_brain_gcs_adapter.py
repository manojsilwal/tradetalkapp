"""GCS storage adapter against a fake client (offline, no network)."""
import unittest

from backend.brain.ports.base import StoragePort
from backend.brain.ports.gcs_adapter import GCSStorage


class _FakeBlob:
    def __init__(self, store, name):
        self._store = store
        self.name = name

    def upload_from_string(self, data, content_type=None):
        self._store[self.name] = data if isinstance(data, bytes) else data.encode()

    def download_as_bytes(self):
        return self._store[self.name]

    def exists(self):
        return self.name in self._store


class _FakeBucket:
    def __init__(self, store, client):
        self._store = store
        self.client = client

    def blob(self, name):
        return _FakeBlob(self._store, name)


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def bucket(self, name):
        return _FakeBucket(self._store, self)

    def list_blobs(self, bucket_name, prefix=""):
        return [_FakeBlob(self._store, n) for n in self._store if n.startswith(prefix)]


class TestGCSStorage(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.adapter = GCSStorage(bucket="b", prefix="brain", client=_FakeClient(self.store))

    def test_is_storage_port(self):
        self.assertIsInstance(self.adapter, StoragePort)

    def test_put_prefixes_key(self):
        uri = self.adapter.put("predictions/2026-06-21/AAPL.json", b"{}")
        self.assertIn("brain/predictions/2026-06-21/AAPL.json", self.store)
        self.assertTrue(uri.startswith("gs://b/brain/"))

    def test_get_roundtrip(self):
        self.adapter.put("model_artifacts/m-v1/meta.json", b'{"x":1}')
        self.assertEqual(self.adapter.get("model_artifacts/m-v1/meta.json"), b'{"x":1}')

    def test_exists(self):
        self.assertFalse(self.adapter.exists("a.json"))
        self.adapter.put("a.json", b"1")
        self.assertTrue(self.adapter.exists("a.json"))

    def test_list_strips_prefix(self):
        self.adapter.put("predictions/2026-06-21/AAPL.json", b"1")
        self.adapter.put("predictions/2026-06-21/MSFT.json", b"1")
        keys = list(self.adapter.list("predictions/"))
        self.assertIn("predictions/2026-06-21/AAPL.json", keys)
        self.assertIn("predictions/2026-06-21/MSFT.json", keys)
        # logical keys do not leak the bucket prefix
        self.assertFalse(any(k.startswith("brain/") for k in keys))


if __name__ == "__main__":
    unittest.main()
