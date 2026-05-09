"""Safe path resolution under TRADETALK_ROOT."""

from __future__ import annotations

import os
from pathlib import Path


class PathSecurityError(ValueError):
    pass


def resolve_under_root(repo_root: str, relative_path: str) -> Path:
    """Resolve a repo-relative path; reject absolute paths and traversal."""
    root = Path(repo_root).resolve()
    rel = (relative_path or ".").strip()
    if os.path.isabs(rel):
        raise PathSecurityError("absolute paths are not allowed")
    if ".." in Path(rel).parts:
        raise PathSecurityError("path traversal is not allowed")

    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise PathSecurityError("path escapes repository root") from e
    return candidate


def read_text_capped(repo_root: str, relative_path: str, max_bytes: int) -> str:
    path = resolve_under_root(repo_root, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {relative_path}")
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        return text + f"\n\n[truncated at {max_bytes} bytes]"
    return data.decode("utf-8", errors="replace")
