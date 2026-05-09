"""TradeTalk MCP server (stdio) — FastMCP wiring."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from tradetalk_mcp.action_registry import ActionRegistry, load_action_registry
from tradetalk_mcp.clients.tradetalk_api import audit_action
from tradetalk_mcp.config import Settings
from tradetalk_mcp.security.http import HttpPolicyError, RateLimiter, api_request, fetch_openapi
from tradetalk_mcp.security.paths import PathSecurityError, read_text_capped, resolve_under_root
from tradetalk_mcp.security.permissions import ActionGate


def _configure_logging(level: str) -> None:
    lvl = getattr(logging, level, logging.WARNING)
    h = logging.StreamHandler(sys.stderr)
    logging.basicConfig(level=lvl, format="%(message)s", handlers=[h], force=True)


@dataclass
class RuntimeContext:
    settings: Settings
    gate: ActionGate
    limiter: RateLimiter
    registry: ActionRegistry


def _load_context_index(repo_root: str) -> dict[str, Any] | None:
    p = Path(repo_root) / ".tradetalk" / "context-index.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def build_mcp() -> FastMCP:
    settings = Settings.from_environ()
    _configure_logging(settings.log_level)
    gate = ActionGate(settings)
    limiter = RateLimiter(settings.rate_limit_ms / 1000.0)
    registry = load_action_registry(settings.repo_root)
    rt = RuntimeContext(settings=settings, gate=gate, limiter=limiter, registry=registry)

    instructions = (
        "TradeTalk MCP: use get_mcp_status first. Context tools read the repo under TRADETALK_ROOT. "
        "Service tools only call allowlisted paths from .tradetalk/mcp-actions.json. "
        "Do not assume mutating actions are enabled unless TRADETALK_MCP_ACTIONS_ENABLED=true."
    )
    mcp = FastMCP(
        name="tradetalk",
        instructions=instructions,
        log_level=settings.log_level if settings.log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else "WARNING",
    )

    # --- Resources (static URIs) ---
    @mcp.resource("tradetalk://docs/ARCHITECTURE.md", mime_type="text/markdown")
    def res_architecture() -> str:
        return read_text_capped(rt.settings.repo_root, "docs/ARCHITECTURE.md", rt.settings.max_read_bytes)

    @mcp.resource("tradetalk://docs/README.md", mime_type="text/markdown")
    def res_readme() -> str:
        return read_text_capped(rt.settings.repo_root, "README.md", rt.settings.max_read_bytes)

    @mcp.resource("tradetalk://docs/AGENTS.md", mime_type="text/markdown")
    def res_agents() -> str:
        return read_text_capped(rt.settings.repo_root, "AGENTS.md", rt.settings.max_read_bytes)

    @mcp.resource("tradetalk://docs/CLAUDE.md", mime_type="text/markdown")
    def res_claude() -> str:
        return read_text_capped(rt.settings.repo_root, "CLAUDE.md", rt.settings.max_read_bytes)

    @mcp.resource("tradetalk://generated/context-index", mime_type="application/json")
    def res_context_index() -> str:
        p = Path(rt.settings.repo_root) / ".tradetalk" / "context-index.json"
        if not p.is_file():
            return json.dumps({"error": "context-index.json not found; run scripts/generate_context_index.py"})
        return p.read_text(encoding="utf-8")

    # --- Context tools ---
    @mcp.tool()
    def read_repo_file(relative_path: str) -> str:
        """Read a UTF-8 text file relative to TRADETALK_ROOT (capped by TRADETALK_MAX_READ_BYTES)."""
        try:
            return read_text_capped(rt.settings.repo_root, relative_path, rt.settings.max_read_bytes)
        except PathSecurityError as e:
            return json.dumps({"error": str(e)})
        except OSError as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def list_dir(relative_path: str = ".") -> str:
        """List files and subdirectories (non-recursive) under relative_path."""
        try:
            base = resolve_under_root(rt.settings.repo_root, relative_path)
        except PathSecurityError as e:
            return json.dumps({"error": str(e)})
        if not base.is_dir():
            return json.dumps({"error": f"not a directory: {relative_path}"})
        entries: list[dict[str, Any]] = []
        cap = 500
        for i, child in enumerate(sorted(base.iterdir(), key=lambda p: p.name.lower())):
            if i >= cap:
                entries.append({"name": "...", "note": f"truncated at {cap} entries"})
                break
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                }
            )
        return json.dumps({"path": relative_path, "entries": entries})

    @mcp.tool()
    def list_routers() -> str:
        """List Python files in backend/routers/."""
        routers = Path(rt.settings.repo_root) / "backend" / "routers"
        if not routers.is_dir():
            return json.dumps({"error": "backend/routers not found", "files": []})
        files = sorted([p.name for p in routers.glob("*.py") if p.is_file()])
        return json.dumps({"files": files})

    @mcp.tool()
    def get_architecture_index() -> str:
        """Return the generated .tradetalk/context-index.json object, or an error object."""
        data = _load_context_index(rt.settings.repo_root)
        if data is None:
            return json.dumps({"error": "missing or invalid .tradetalk/context-index.json"})
        return json.dumps(data)

    @mcp.tool()
    def get_backend_map() -> str:
        """Return the backend section of the context index."""
        data = _load_context_index(rt.settings.repo_root)
        if not data:
            return json.dumps({"error": "context index unavailable"})
        return json.dumps(data.get("backend", {}))

    @mcp.tool()
    def get_service_catalog() -> str:
        """Return services / catalog section from context index if present."""
        data = _load_context_index(rt.settings.repo_root)
        if not data:
            return json.dumps({"error": "context index unavailable"})
        return json.dumps(data.get("services", data.get("catalog", {})))

    @mcp.tool()
    def get_router_summary(router_name: str = "") -> str:
        """Summarize routers from context index; optional filter by substring of file name or prefix."""
        data = _load_context_index(rt.settings.repo_root)
        if not data:
            return json.dumps({"error": "context index unavailable"})
        routers = data.get("backend", {}).get("routers", [])
        if not isinstance(routers, list):
            return json.dumps({"routers": []})
        if router_name.strip():
            q = router_name.strip().lower()
            routers = [r for r in routers if q in json.dumps(r).lower()]
        return json.dumps({"routers": routers})

    @mcp.tool()
    def fetch_openapi_json() -> str:
        """Fetch OpenAPI JSON from TRADETALK_OPENAPI_URL (discovery only; host must match allowlist)."""
        try:
            text = fetch_openapi(rt.settings.openapi_url, rt.settings.api_host_allowlist)
            return text
        except HttpPolicyError as e:
            return json.dumps({"error": str(e), "openapi_url": rt.settings.openapi_url})
        except Exception as e:
            return json.dumps({"error": str(e), "openapi_url": rt.settings.openapi_url})

    @mcp.tool()
    def get_mcp_status() -> str:
        """Status: mode, paths, context index freshness, OpenAPI URL, action registry counts."""
        idx_path = Path(rt.settings.repo_root) / ".tradetalk" / "context-index.json"
        idx = _load_context_index(rt.settings.repo_root)
        generated_at = None
        stale_hint = None
        if isinstance(idx, dict):
            generated_at = idx.get("generated_at")
        exists = idx_path.is_file()
        reg = rt.registry
        enabled_actions = [a for a in reg.actions if a.enabled]
        return json.dumps(
            {
                "repo_root": rt.settings.repo_root,
                "mode": rt.settings.mode,
                "context_index_exists": exists,
                "context_index_generated_at": generated_at,
                "context_index_stale": stale_hint,
                "openapi_url": rt.settings.openapi_url,
                "api_base_url": rt.settings.api_base_url,
                "actions_enabled": rt.settings.actions_enabled,
                "dry_run": rt.settings.dry_run,
                "approved_actions_count": len(enabled_actions),
                "service_tools_visible": rt.gate.service_tools_visible(),
            },
            indent=2,
        )

    @mcp.tool()
    def list_available_backend_routes() -> str:
        """List HTTP routes from context index (preferred) or suggest fetching OpenAPI."""
        data = _load_context_index(rt.settings.repo_root)
        if data and data.get("backend", {}).get("routers"):
            routes: list[str] = []
            for r in data["backend"]["routers"]:
                if isinstance(r, dict):
                    for ep in r.get("endpoints", []) or []:
                        routes.append(str(ep))
            return json.dumps({"source": "context-index", "routes": sorted(set(routes))})
        return json.dumps(
            {
                "source": "none",
                "hint": "Run scripts/generate_context_index.py or call fetch_openapi_json with a running API",
                "routes": [],
            }
        )

    def _service_tool_error(msg: str) -> str:
        return json.dumps({"error": msg})

    def health_check_backend() -> str:
        """GET allowlisted health path (see mcp-actions.json: health_check_backend)."""
        ok, reason = rt.gate.can_call_action(mutates=False, requires_actions_enabled=False)
        if not ok:
            return _service_tool_error(reason)
        act = rt.registry.by_name().get("health_check_backend")
        if not act:
            return _service_tool_error("health_check_backend not in registry")
        if rt.settings.dry_run:
            audit_action("health_check_backend", {"dry_run": True})
            return json.dumps({"dry_run": True, "would": {"method": act.method, "path": act.path}})
        rt.limiter.wait("health_check_backend")
        try:
            code, body = api_request(
                rt.settings.api_base_url,
                act.path,
                method=act.method,
                json_body=None,
                api_key=rt.settings.api_key,
                host_allowlist=rt.settings.api_host_allowlist,
            )
            audit_action("health_check_backend", {"http_status": code})
            return json.dumps({"http_status": code, "body_preview": body[:8000]})
        except HttpPolicyError as e:
            return _service_tool_error(str(e))

    def get_service_status() -> str:
        """GET pipeline / service status endpoint from allowlist."""
        ok, reason = rt.gate.can_call_action(mutates=False, requires_actions_enabled=False)
        if not ok:
            return _service_tool_error(reason)
        act = rt.registry.by_name().get("get_service_status")
        if not act:
            return _service_tool_error("get_service_status not in registry")
        if rt.settings.dry_run:
            audit_action("get_service_status", {"dry_run": True})
            return json.dumps({"dry_run": True})
        rt.limiter.wait("get_service_status")
        try:
            code, body = api_request(
                rt.settings.api_base_url,
                act.path,
                method=act.method,
                json_body=None,
                api_key=rt.settings.api_key,
                host_allowlist=rt.settings.api_host_allowlist,
            )
            audit_action("get_service_status", {"http_status": code})
            return json.dumps({"http_status": code, "body_preview": body[:8000]})
        except HttpPolicyError as e:
            return _service_tool_error(str(e))

    def list_approved_actions() -> str:
        """List entries from .tradetalk/mcp-actions.json (metadata only)."""
        out = [
            {
                "name": a.name,
                "method": a.method,
                "path": a.path,
                "enabled": a.enabled,
                "mutates": a.mutates,
                "requires_actions_enabled": a.requires_actions_enabled,
            }
            for a in rt.registry.actions
        ]
        return json.dumps({"actions": out})

    def trigger_approved_action(action_name: str, payload: str = "{}") -> str:
        """Invoke a single allowlisted action by name. payload is JSON string for POST bodies."""
        try:
            body_obj = json.loads(payload) if payload.strip() else {}
            if not isinstance(body_obj, dict):
                return _service_tool_error("payload must be a JSON object")
        except json.JSONDecodeError as e:
            return _service_tool_error(f"invalid JSON payload: {e}")
        act = rt.registry.by_name().get(action_name.strip())
        if not act:
            return _service_tool_error(f"unknown or disabled action: {action_name}")
        ok, reason = rt.gate.can_call_action(
            mutates=act.mutates,
            requires_actions_enabled=act.requires_actions_enabled,
        )
        if not ok:
            return _service_tool_error(reason)
        if rt.settings.dry_run:
            audit_action(action_name, {"dry_run": True, "path": act.path, "method": act.method})
            return json.dumps({"dry_run": True, "action": action_name, "method": act.method, "path": act.path})
        rt.limiter.wait(f"action:{action_name}")
        try:
            jb = body_obj if act.method.upper() != "GET" else None
            code, body = api_request(
                rt.settings.api_base_url,
                act.path,
                method=act.method,
                json_body=jb,
                api_key=rt.settings.api_key,
                host_allowlist=rt.settings.api_host_allowlist,
            )
            audit_action(action_name, {"http_status": code, "path": act.path})
            return json.dumps({"http_status": code, "body_preview": body[:12000]})
        except HttpPolicyError as e:
            return _service_tool_error(str(e))

    def refresh_market_data() -> str:
        """Convenience wrapper for the allowlisted macro snapshot action (GET /macro)."""
        return trigger_approved_action("refresh_macro_read", "{}")

    def run_backtest(strategy: str = "", preset_id: str = "", start_date: str = "2020-01-01", end_date: str = "2024-01-01") -> str:
        """POST /backtest with optional strategy text or preset_id (mutating; requires actions enabled)."""
        pl = {"strategy": strategy, "preset_id": preset_id or None, "start_date": start_date, "end_date": end_date}
        return trigger_approved_action("run_backtest", json.dumps(pl))

    if rt.gate.service_tools_visible():
        mcp.add_tool(health_check_backend)
        mcp.add_tool(get_service_status)
        mcp.add_tool(list_approved_actions)
        mcp.add_tool(trigger_approved_action)
        mcp.add_tool(refresh_market_data)
        mcp.add_tool(run_backtest)

    # --- Prompts ---
    @mcp.prompt()
    def tradetalk_onboarding_prompt(role: str = "new contributor") -> str:
        return (
            f"You are helping a {role} on TradeTalk. Call get_mcp_status, read README and docs/ARCHITECTURE.md "
            f"via resources or read_repo_file, list backend/routers, then summarize how to run backend and frontend."
        )

    @mcp.prompt()
    def tradetalk_router_analysis_prompt(router_name: str = "") -> str:
        return (
            "Analyze TradeTalk HTTP routing for "
            + (router_name or "all routers")
            + ". Use get_router_summary and read_repo_file on backend/main.py and the router module."
        )

    @mcp.prompt()
    def tradetalk_service_action_review_prompt(action_name: str = "") -> str:
        return (
            "Review MCP service action safety for TradeTalk: "
            + (action_name or "all actions in list_approved_actions")
            + ". Confirm allowlist-only execution and when TRADETALK_MCP_ACTIONS_ENABLED is required."
        )

    return mcp


async def main() -> None:
    # Ensure package import works when run as python -m tradetalk_mcp
    os.environ.setdefault("TRADETALK_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    mcp = build_mcp()
    await mcp.run_stdio_async()
