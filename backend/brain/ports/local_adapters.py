"""Local/offline adapters — the free-tier and test defaults.

LocalStorage  -> filesystem (stands in for GCS/S3/Blob)
MemoryCache   -> in-process dict with TTL (stands in for Redis/Supabase)
EnvSecrets    -> os.environ (stands in for Secret Manager/Key Vault)
"""
from __future__ import annotations

import os
import time
from typing import Dict, Iterable, Optional, Tuple


class LocalStorage:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        # Disallow escaping the root.
        safe = key.lstrip("/")
        full = os.path.abspath(os.path.join(self.root, safe))
        if not full.startswith(self.root):
            raise ValueError(f"key escapes storage root: {key!r}")
        return full

    def put(self, key: str, data: bytes, *, content_type: Optional[str] = None) -> str:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return f"file://{path}"

    def get(self, key: str) -> bytes:
        with open(self._path(key), "rb") as f:
            return f.read()

    def list(self, prefix: str) -> Iterable[str]:
        base = self._path(prefix)
        root_for_rel = self.root
        results = []
        if os.path.isdir(base):
            walk_root = base
        else:
            walk_root = os.path.dirname(base)
        for dirpath, _dirs, files in os.walk(walk_root):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root_for_rel)
                if rel.replace(os.sep, "/").startswith(prefix.lstrip("/")):
                    results.append(rel.replace(os.sep, "/"))
        return sorted(results)

    def exists(self, key: str) -> bool:
        return os.path.exists(self._path(key))


class MemoryCache:
    def __init__(self) -> None:
        self._store: Dict[str, Tuple[bytes, Optional[float]]] = {}

    def get(self, key: str) -> Optional[bytes]:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and time.time() > expires:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: bytes, *, ttl_s: Optional[int] = None) -> None:
        expires = (time.time() + ttl_s) if ttl_s else None
        self._store[key] = (value, expires)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class EnvSecrets:
    def resolve(self, key: str) -> Optional[str]:
        return os.environ.get(key)
