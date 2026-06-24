"""Google Cloud Storage adapter implementing the brain's StoragePort.

Stores brain artifacts (model registry, per-ticker snapshots, status manifest)
in a GCS bucket under an optional key prefix. Keys passed to/returned from this
adapter are *logical* keys (e.g. ``predictions/2026-06-22/AAPL.json``); the
bucket prefix is added/stripped transparently so callers are identical to the
local adapter.

The google-cloud-storage client is imported and constructed lazily so importing
the brain package never requires the dependency or live credentials (keeps the
offline default and tests working).
"""
from __future__ import annotations

from typing import Iterable, Optional


class GCSStorage:
    """Object storage backed by a GCS bucket (StoragePort-compatible)."""

    def __init__(self, bucket: str, prefix: str = "", *, client=None):
        if not bucket:
            raise ValueError("GCSStorage requires a bucket name")
        self.bucket_name = bucket
        # Normalize prefix to either "" or "something/".
        prefix = (prefix or "").strip("/")
        self.prefix = f"{prefix}/" if prefix else ""
        self._client = client
        self._bucket = None

    # --- client plumbing (lazy) -------------------------------------------
    @property
    def bucket(self):
        if self._bucket is None:
            if self._client is None:
                from google.cloud import storage  # lazy import
                self._client = storage.Client()
            self._bucket = self._client.bucket(self.bucket_name)
        return self._bucket

    def _blob_name(self, key: str) -> str:
        return f"{self.prefix}{key.lstrip('/')}"

    def _logical_key(self, blob_name: str) -> str:
        if self.prefix and blob_name.startswith(self.prefix):
            return blob_name[len(self.prefix):]
        return blob_name

    # --- StoragePort ------------------------------------------------------
    def put(self, key: str, data: bytes, *, content_type: Optional[str] = None) -> str:
        blob = self.bucket.blob(self._blob_name(key))
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
        return f"gs://{self.bucket_name}/{self._blob_name(key)}"

    def get(self, key: str) -> bytes:
        blob = self.bucket.blob(self._blob_name(key))
        return blob.download_as_bytes()

    def list(self, prefix: str) -> Iterable[str]:
        blob_prefix = self._blob_name(prefix)
        return sorted(
            self._logical_key(b.name)
            for b in self.bucket.client.list_blobs(self.bucket_name, prefix=blob_prefix)
        )

    def exists(self, key: str) -> bool:
        return self.bucket.blob(self._blob_name(key)).exists()
