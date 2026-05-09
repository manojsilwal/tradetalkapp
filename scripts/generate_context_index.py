#!/usr/bin/env python3
"""Generate .tradetalk/context-index.json for MCP and docs freshness."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _scan_router_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    prefix_m = re.search(r'router\s*=\s*APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']+)["\']', text)
    prefix = prefix_m.group(1) if prefix_m else ""
    endpoints: list[str] = []
    for m in re.finditer(
        r'@router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']*)["\']',
        text,
        re.IGNORECASE,
    ):
        method, sub = m.group(1).upper(), m.group(2) or ""
        full = f"{method} {prefix}{sub}" if prefix else f"{method} {sub or '/'}"
        endpoints.append(full.strip())
    rel = str(path.relative_to(_REPO_ROOT))
    return {"file": rel, "prefix": prefix or "/", "endpoints": sorted(set(endpoints))}


def _scan_main_routers() -> list[str]:
    main_py = _REPO_ROOT / "backend" / "main.py"
    if not main_py.is_file():
        return []
    text = main_py.read_text(encoding="utf-8", errors="replace")
    return sorted(set(re.findall(r"app\.include_router\(([\w.]+)\)", text)))


def _frontend_routes() -> list[str]:
    app_jsx = _REPO_ROOT / "frontend" / "src" / "App.jsx"
    if not app_jsx.is_file():
        return []
    text = app_jsx.read_text(encoding="utf-8", errors="replace")
    return sorted(set(re.findall(r'<Route\s+path=["\']([^"\']+)["\']', text)))


def _list_docs() -> list[str]:
    out: list[str] = []
    for name in ("README.md", "AGENTS.md", "CLAUDE.md"):
        p = _REPO_ROOT / name
        if p.is_file():
            out.append(name)
    docs_dir = _REPO_ROOT / "docs"
    if docs_dir.is_dir():
        out.extend(sorted(str(p.relative_to(_REPO_ROOT)) for p in docs_dir.rglob("*.md")))
    return sorted(set(out))


def _optional_openapi() -> dict | None:
    url = os.environ.get("TRADETALK_OPENAPI_URL", "").strip()
    if not url:
        return None
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        paths = list(data.get("paths", {}).keys()) if isinstance(data, dict) else []
        return {"source": url, "path_count": len(paths), "paths_sample": sorted(paths)[:80]}
    except Exception as e:
        return {"source": url, "error": str(e)}


def main() -> int:
    routers_dir = _REPO_ROOT / "backend" / "routers"
    routers: list[dict] = []
    if routers_dir.is_dir():
        for py in sorted(routers_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            routers.append(_scan_router_file(py))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "backend": {
            "entrypoint": "backend/main.py",
            "include_router_hints": _scan_main_routers(),
            "routers": routers,
        },
        "frontend": {
            "framework": "vite/react",
            "routes": _frontend_routes(),
        },
        "docs": _list_docs(),
        "services": [
            "knowledge_store",
            "llm_client",
            "market_intel",
            "daily_pipeline",
            "backtest_engine",
        ],
        "openapi_hint": _optional_openapi(),
    }

    out_dir = _REPO_ROOT / ".tradetalk"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "context-index.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path.relative_to(_REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
