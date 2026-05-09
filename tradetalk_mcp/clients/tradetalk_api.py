"""Audit logging for MCP-triggered API calls."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

log = logging.getLogger("tradetalk_mcp.audit")


def audit_action(name: str, detail: dict[str, Any]) -> None:
    line = json.dumps({"mcp_audit": True, "action": name, **detail}, default=str)
    print(line, file=sys.stderr, flush=True)
