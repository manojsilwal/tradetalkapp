#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pick_python() {
  for candidate in "$ROOT/.venv/bin/python" "$ROOT/.venv-py312/bin/python" python3.12 python3.11 python3.10 python3; do
    if ! command -v "$candidate" >/dev/null 2>&1 && [[ ! -x "$candidate" ]]; then
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
  echo "start_local.sh: need Python 3.10+." >&2
  exit 1
}

cleanup() {
  echo "Stopping local servers..."
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export PYTHONPATH="$ROOT"
export PORT="${PORT:-8000}"

echo "Starting backend server on http://127.0.0.1:$PORT..."
"$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT" &
BACK_PID=$!

echo "Starting frontend Vite dev server on http://127.0.0.1:5173..."
cd "$ROOT/frontend"
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort &
FRONT_PID=$!

echo "============================================="
echo "TradeTalk application is running locally!"
echo "Backend API: http://127.0.0.1:$PORT"
echo "Frontend App: http://127.0.0.1:5173"
echo "Press Ctrl+C to terminate both servers."
echo "============================================="

wait $BACK_PID $FRONT_PID
