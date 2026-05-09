"""python -m tradetalk_mcp — run MCP server over stdio."""

from __future__ import annotations

import asyncio

from tradetalk_mcp.server import main

if __name__ == "__main__":
    asyncio.run(main())
