#!/usr/bin/env python3
"""Validate .tradetalk/mcp-actions.json (exit non-zero on error)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from tradetalk_mcp.action_registry import validate_registry_schema  # noqa: E402


def main() -> int:
    errs = validate_registry_schema(str(_REPO))
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 1
    print("mcp-actions.json OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
