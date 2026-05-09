#!/usr/bin/env bash
# Run TradeTalk MCP over stdio (install deps first: pip install -r requirements-mcp.txt).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export TRADETALK_ROOT="${TRADETALK_ROOT:-$ROOT}"
export PYTHONPATH="${PYTHONPATH:-}:$ROOT"
exec python3 -m tradetalk_mcp
