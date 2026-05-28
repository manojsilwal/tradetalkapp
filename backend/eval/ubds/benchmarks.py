from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def load_benchmark_config(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "evals" / "configs" / "ubds_benchmark.yaml"
    if not path.is_file():
        return {"median_time_on_task_ms": 45000, "tasks": {}}
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        return {"median_time_on_task_ms": 45000, "tasks": {}}
    data = yaml.safe_load(text) or {}
    return data


def median_benchmark_ms(config: dict[str, Any]) -> float:
    return float(config.get("median_time_on_task_ms", 45000))


def task_benchmark_ms(config: dict[str, Any], task_id: str) -> float | None:
    tasks = config.get("tasks") or {}
    if task_id in tasks:
        return float(tasks[task_id])
    return None
