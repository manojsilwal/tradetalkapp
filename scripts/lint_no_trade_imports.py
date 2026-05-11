#!/usr/bin/env python3
"""
Fail if ``backend/predictor/**`` imports trading execution modules.

Phase 6 guardrail — run in CI alongside unit tests:

    PYTHONPATH=. python scripts/lint_no_trade_imports.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRED_DIR = ROOT / "backend" / "predictor"

FORBIDDEN_SUBSTRINGS = (
    "paper_portfolio",
    "backtest_engine",
    ".broker",
    "broker.",
    "order_execute",
)


def _check_file(path: Path) -> list[str]:
    errors: list[str] = []
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for sub in FORBIDDEN_SUBSTRINGS:
                if sub in mod:
                    errors.append(f"{path}: forbidden import from {mod}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                for sub in FORBIDDEN_SUBSTRINGS:
                    if sub in name:
                        errors.append(f"{path}: forbidden import {name}")
    return errors


def main() -> int:
    if not PRED_DIR.is_dir():
        print("lint_no_trade_imports: predictor dir missing", file=sys.stderr)
        return 0
    all_err: list[str] = []
    for py in sorted(PRED_DIR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        all_err.extend(_check_file(py))
    if all_err:
        print("\n".join(all_err), file=sys.stderr)
        return 1
    print("lint_no_trade_imports: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
