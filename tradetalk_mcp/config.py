"""Environment-driven configuration for the TradeTalk MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

McpMode = Literal["context", "actions", "full"]


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    repo_root: str
    mode: McpMode
    api_base_url: str
    openapi_url: str
    actions_enabled: bool
    api_key: str
    max_read_bytes: int
    log_level: str
    dry_run: bool
    rate_limit_ms: int
    api_host_allowlist: frozenset[str]

    @classmethod
    def from_environ(cls) -> Settings:
        root = os.environ.get("TRADETALK_ROOT", "").strip()
        if not root:
            # Default: parent of tradetalk_mcp package (repository root)
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        mode_raw = os.environ.get("TRADETALK_MCP_MODE", "context").strip().lower()
        if mode_raw not in ("context", "actions", "full"):
            mode_raw = "context"
        mode: McpMode = mode_raw  # type: ignore[assignment]

        api_base = os.environ.get("TRADETALK_API_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
        openapi = os.environ.get("TRADETALK_OPENAPI_URL", "").strip()
        if not openapi:
            openapi = f"{api_base}/openapi.json"

        allow_raw = os.environ.get("TRADETALK_API_HOST_ALLOWLIST", "").strip()
        hosts: set[str] = set()
        if allow_raw:
            for h in allow_raw.split(","):
                h = h.strip().lower()
                if h:
                    hosts.add(h)
        else:
            from urllib.parse import urlparse

            p = urlparse(api_base)
            if p.hostname:
                hosts.add(p.hostname.lower())

        return cls(
            repo_root=os.path.abspath(root),
            mode=mode,
            api_base_url=api_base,
            openapi_url=openapi,
            actions_enabled=_env_bool("TRADETALK_MCP_ACTIONS_ENABLED", False),
            api_key=os.environ.get("TRADETALK_MCP_API_KEY", "").strip(),
            max_read_bytes=max(1024, _env_int("TRADETALK_MAX_READ_BYTES", 512_000)),
            log_level=os.environ.get("TRADETALK_MCP_LOG_LEVEL", "WARNING").strip().upper(),
            dry_run=_env_bool("TRADETALK_MCP_DRY_RUN", False),
            rate_limit_ms=max(0, _env_int("TRADETALK_MCP_RATE_LIMIT_MS", 500)),
            api_host_allowlist=frozenset(hosts),
        )


def settings_singleton() -> Settings:
    return Settings.from_environ()
