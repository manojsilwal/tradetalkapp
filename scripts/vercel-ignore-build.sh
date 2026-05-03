#!/usr/bin/env bash
# Vercel "Ignored Build Step" / vercel.json ignoreCommand semantics:
# - exit 0 => skip this deployment (nothing relevant changed)
# - exit 1 => run the build
#
# Repo root is required so paths match the GitHub clone layout.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${ROOT}" ]]; then
  exit 1
fi
cd "${ROOT}"

if ! git rev-parse --verify HEAD^ >/dev/null 2>&1; then
  exit 1
fi

# Widen scope vs `frontend/` only so infra edits (root vercel.json, deploy workflow)
# still produce a fresh frontend bundle when needed.
if git diff HEAD^ HEAD --quiet -- \
  frontend/ \
  vercel.json \
  .github/workflows/vercel-production-deploy.yml \
  scripts/vercel-ignore-build.sh
then
  exit 0
else
  exit 1
fi
