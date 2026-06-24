"""Cloud portability ports (see docs/CLOUD_PORTABILITY.md).

Every cloud call goes through a thin port interface; adapters are selected by env
(``CLOUD_PROVIDER`` / ``*_BACKEND``). Defaults are local/offline so the brain
runs and tests with zero cloud dependencies.
"""
from .base import StoragePort, CachePort, SecretsPort
from .factory import get_storage, get_cache, get_secrets

__all__ = [
    "StoragePort", "CachePort", "SecretsPort",
    "get_storage", "get_cache", "get_secrets",
]
