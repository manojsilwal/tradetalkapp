# `tradetalk_mcp`

Platform-agnostic **Model Context Protocol** (stdio) server for the TradeTalk repo.

Full documentation: **[docs/MCP.md](../docs/MCP.md)** (install, Cursor/VS Code/Claude, env vars, tools, CI, security).

Run:

```bash
pip install -r ../requirements-mcp.txt
export TRADETALK_ROOT="$(cd .. && pwd)"
PYTHONPATH=.. python -m tradetalk_mcp
```
