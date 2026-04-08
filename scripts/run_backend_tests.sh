#!/usr/bin/env bash
# Run backend unit tests with the first Python >= 3.10 on PATH.
# macOS /usr/bin/python3 is often 3.9 and cannot load this codebase (PEP 604 unions, etc.).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pick_python() {
  for candidate in python3.12 python3.11 python3.10 python3; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  echo "run_backend_tests.sh: need Python 3.10+ on PATH (e.g. python3.12, python3.11)." >&2
  echo "Install with pyenv, Homebrew, or conda; see AGENTS.md." >&2
  exit 1
}

export PYTHONPATH=.
exec "$PY" -m unittest discover -s backend/tests -p 'test_*.py' -v "$@"
