"""Load repo-root ``configs/*.yaml`` for predictor."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
_CONFIGS_DIR = _REPO_ROOT / "configs"


def configs_dir() -> Path:
    return _CONFIGS_DIR


@lru_cache(maxsize=8)
def load_yaml_cached(rel_path: str) -> Dict[str, Any]:
    path = _CONFIGS_DIR / rel_path
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return dict(data) if isinstance(data, dict) else {}
