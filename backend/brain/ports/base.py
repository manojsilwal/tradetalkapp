"""Port protocols. Adapters (gcp/aws/azure/local) implement these structurally."""
from __future__ import annotations

from typing import Iterable, Optional, Protocol, runtime_checkable


@runtime_checkable
class StoragePort(Protocol):
    """Object storage: local | GCS | S3 | Azure Blob."""

    def put(self, key: str, data: bytes, *, content_type: Optional[str] = None) -> str: ...

    def get(self, key: str) -> bytes: ...

    def list(self, prefix: str) -> Iterable[str]: ...

    def exists(self, key: str) -> bool: ...


@runtime_checkable
class CachePort(Protocol):
    """Hot/warm cache: in-memory | Redis | Supabase."""

    def get(self, key: str) -> Optional[bytes]: ...

    def set(self, key: str, value: bytes, *, ttl_s: Optional[int] = None) -> None: ...

    def delete(self, key: str) -> None: ...


@runtime_checkable
class SecretsPort(Protocol):
    """Resolve secrets: env | GSM | ASM | Key Vault."""

    def resolve(self, key: str) -> Optional[str]: ...
