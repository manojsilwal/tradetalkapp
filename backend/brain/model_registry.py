"""Versioned model registry — every prediction traces back to a model version.

A model version is a directory under the registry root containing:
  model.json        - serialized model weights + config
  scaler.json       - the fitted StandardScaler
  feature_list.json - the exact, immutable feature order
  metrics.json      - validation/backtest metrics
  meta.json         - name, version, created_at, hashes, training window

Storage is via the StoragePort so the registry is cloud-portable; the default is
the local filesystem adapter (offline-friendly).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Dict, List, Optional

from .models import BaseModel, model_from_dict
from .scaler import StandardScaler
from .ports.factory import get_storage
from .ports.base import StoragePort


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def feature_list_hash(feature_list: List[str]) -> str:
    return hashlib.sha256("|".join(feature_list).encode()).hexdigest()[:16]


class ModelRegistry:
    def __init__(self, root: str = "model_artifacts", storage: Optional[StoragePort] = None):
        self.root = root.rstrip("/")
        self.storage = storage or get_storage()

    def _key(self, model_name: str, version: str, fname: str) -> str:
        return f"{self.root}/{model_name}-{version}/{fname}"

    def save(self, model: BaseModel, scaler: StandardScaler, feature_list: List[str],
             version: str, metrics: Optional[Dict] = None,
             training_window: Optional[Dict] = None) -> str:
        meta = {
            "model_name": model.name,
            "model_version": version,
            "created_at": _now_iso(),
            "feature_list_hash": feature_list_hash(feature_list),
            "training_window": training_window or {},
        }
        files = {
            "model.json": model.to_dict(),
            "scaler.json": scaler.to_dict(),
            "feature_list.json": feature_list,
            "metrics.json": metrics or {},
            "meta.json": meta,
        }
        for fname, payload in files.items():
            self.storage.put(
                self._key(model.name, version, fname),
                json.dumps(payload, indent=2).encode(),
                content_type="application/json",
            )
        return f"{self.root}/{model.name}-{version}/"

    def load(self, model_name: str, version: str):
        model = model_from_dict(self._read(model_name, version, "model.json"))
        scaler = StandardScaler.from_dict(self._read(model_name, version, "scaler.json"))
        feature_list = self._read(model_name, version, "feature_list.json")
        meta = self._read(model_name, version, "meta.json")
        metrics = self._read(model_name, version, "metrics.json")
        # Integrity guard: feature list must match the hash stamped at save time.
        if feature_list_hash(feature_list) != meta.get("feature_list_hash"):
            raise ValueError("feature_list hash mismatch — artifact corrupted")
        return {
            "model": model, "scaler": scaler, "feature_list": feature_list,
            "meta": meta, "metrics": metrics, "model_version": version,
        }

    def _read(self, model_name: str, version: str, fname: str):
        raw = self.storage.get(self._key(model_name, version, fname))
        return json.loads(raw.decode())

    def exists(self, model_name: str, version: str) -> bool:
        return self.storage.exists(self._key(model_name, version, "meta.json"))
