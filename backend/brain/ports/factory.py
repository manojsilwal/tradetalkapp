"""Port factories — select adapter by env, default to local/offline.

    CLOUD_PROVIDER   = local | gcp | aws | azure   (default: local)
    STORAGE_BACKEND  = local | gcp | aws | azure    (overrides for storage)
    CACHE_BACKEND    = memory | supabase | redis     (default: memory)
    BRAIN_STORAGE_ROOT = path for local storage (default: ./brain_storage)

Only the local/memory/env adapters are implemented here; cloud adapters are
declared in docs/CLOUD_PORTABILITY.md and plug in without touching callers.
"""
from __future__ import annotations

import os

from .base import CachePort, SecretsPort, StoragePort
from .local_adapters import EnvSecrets, LocalStorage, MemoryCache

# Module-level singleton so the in-memory cache is shared within a process.
_cache_singleton: CachePort | None = None


def _provider(default: str = "local") -> str:
    return os.environ.get("CLOUD_PROVIDER", default).lower()


def _storage_root() -> str:
    # Read at call time so tests can point it at a temp dir.
    return os.environ.get("BRAIN_STORAGE_ROOT", "brain_storage")


def get_storage(root: str | None = None) -> StoragePort:
    backend = os.environ.get("STORAGE_BACKEND", _provider()).lower()
    if backend in ("local", "file"):
        return LocalStorage(root or _storage_root())
    # Cloud adapters declared in docs/CLOUD_PORTABILITY.md.
    raise NotImplementedError(
        f"STORAGE_BACKEND={backend!r} adapter not bundled; see docs/CLOUD_PORTABILITY.md"
    )


def get_cache() -> CachePort:
    global _cache_singleton
    backend = os.environ.get("CACHE_BACKEND", "memory").lower()
    if backend == "memory":
        if _cache_singleton is None:
            _cache_singleton = MemoryCache()
        return _cache_singleton
    raise NotImplementedError(
        f"CACHE_BACKEND={backend!r} adapter not bundled; see docs/CLOUD_PORTABILITY.md"
    )


def get_secrets() -> SecretsPort:
    backend = os.environ.get("SECRETS_BACKEND", "env").lower()
    if backend == "env":
        return EnvSecrets()
    raise NotImplementedError(
        f"SECRETS_BACKEND={backend!r} adapter not bundled; see docs/CLOUD_PORTABILITY.md"
    )
