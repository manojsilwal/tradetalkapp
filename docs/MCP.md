# TradeTalk Model Context Protocol (MCP)

This repository includes a **stdio MCP server** ([`tradetalk_mcp`](../tradetalk_mcp/)) so any MCP-capable client (Cursor, Claude Desktop, VS Code, and others) can load **fresh** TradeTalk context from disk and optionally call **allowlisted** backend HTTP actions.

Dependencies are isolated in [`requirements-mcp.txt`](../requirements-mcp.txt) (not installed on the Render API image).

---

## Quick start

```bash
cd /path/to/tradetalkapp
python3 -m venv .venv-mcp
.venv-mcp/bin/pip install -r requirements-mcp.txt
export TRADETALK_ROOT="$(pwd)"
export TRADETALK_MCP_MODE=context
PYTHONPATH=. .venv-mcp/bin/python -m tradetalk_mcp
```

The process speaks MCP over **stdin/stdout**. Do not attach a TTY that prints extra text to stdout; logs go to **stderr**.

---

## Cursor

1. Create or use **[`.cursor/mcp.json`](../.cursor/mcp.json)** (committed in this repo).
2. Ensure **`.venv-mcp`** exists and has deps:  
   `python3 -m venv .venv-mcp && .venv-mcp/bin/pip install -r requirements-mcp.txt`
3. Restart Cursor. Enable the server under **Settings → Features → Model Context Protocol**.
4. Use **Output → MCP Logs** if the server fails to start (wrong `command` path, missing venv).

Interpolation (see [Cursor MCP docs](https://cursor.com/docs/context/mcp)): `${workspaceFolder}` is the project root.

Profiles in `.cursor/mcp.json`:

- **`tradetalk-context`** — repo/docs/index/OpenAPI discovery only; **no** service HTTP tools.
- **`tradetalk-actions`** — same binary with **`TRADETALK_MCP_MODE=full`**; turn on **`TRADETALK_MCP_ACTIONS_ENABLED=true`** when you want mutating calls and have the API running.

---

## VS Code / GitHub Copilot

Use **[`.vscode/mcp.json`](../.vscode/mcp.json)**. Schema uses a top-level **`servers`** object (not `mcpServers`). Same Python entrypoint as above.

---

## Claude Desktop

Copy **[`examples/mcp/claude_desktop_config.example.json`](../examples/mcp/claude_desktop_config.example.json)** into your Claude config and set **`TRADETALK_ROOT`** to an absolute path.

---

## Runtime modes

| `TRADETALK_MCP_MODE` | Context tools | Service tools (HTTP) |
|----------------------|---------------|----------------------|
| `context` (default)  | Yes           | Not registered       |
| `actions`            | Yes           | Yes (gated)          |
| `full`               | Yes           | Yes (gated)          |

Mutating actions require **`TRADETALK_MCP_ACTIONS_ENABLED=true`** in addition to the registry entry (see [`.tradetalk/mcp-actions.json`](../.tradetalk/mcp-actions.json)).

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TRADETALK_ROOT` | Repository root (required for correct reads; defaults to parent of `tradetalk_mcp` if unset). |
| `TRADETALK_MCP_MODE` | `context` \| `actions` \| `full`. |
| `TRADETALK_API_BASE_URL` | Base URL for allowlisted HTTP tools (default `http://127.0.0.1:8000`). |
| `TRADETALK_OPENAPI_URL` | OpenAPI JSON URL (default `{API_BASE}/openapi.json`). |
| `TRADETALK_MCP_ACTIONS_ENABLED` | `true` to allow mutating registry actions. |
| `TRADETALK_MCP_API_KEY` | Optional; sent as `X-TradeTalk-MCP-Key` if set. |
| `TRADETALK_API_HOST_ALLOWLIST` | Optional comma-separated hostnames; default derives from `TRADETALK_API_BASE_URL`. |
| `TRADETALK_MAX_READ_BYTES` | Cap for `read_repo_file` (default 512000). |
| `TRADETALK_MCP_LOG_LEVEL` | `DEBUG` … `CRITICAL` (default `WARNING`). |
| `TRADETALK_MCP_DRY_RUN` | `true` — log audit lines but do not perform HTTP for actions. |
| `TRADETALK_MCP_RATE_LIMIT_MS` | Minimum milliseconds between calls per tool key (default 500). |

---

## Resources

| URI | Content |
|-----|---------|
| `tradetalk://docs/ARCHITECTURE.md` | Architecture doc |
| `tradetalk://docs/README.md` | Root README |
| `tradetalk://docs/AGENTS.md` | Agent/release loop |
| `tradetalk://docs/CLAUDE.md` | Cursor agent persona |
| `tradetalk://generated/context-index` | [`.tradetalk/context-index.json`](../.tradetalk/context-index.json) |

---

## Tools (summary)

**Context (always in `context` mode):**  
`read_repo_file`, `list_dir`, `list_routers`, `get_architecture_index`, `get_backend_map`, `get_service_catalog`, `get_router_summary`, `fetch_openapi_json`, `get_mcp_status`, `list_available_backend_routes`.

**Service (`actions` / `full` only):**  
`health_check_backend`, `get_service_status`, `list_approved_actions`, `trigger_approved_action`, `refresh_market_data`, `run_backtest`.

Execution is **never** arbitrary HTTP: only methods/paths in **`.tradetalk/mcp-actions.json`**. Service calls emit JSON audit lines to **stderr** with `"mcp_audit": true`.

---

## Prompts

- `tradetalk_onboarding_prompt`
- `tradetalk_router_analysis_prompt`
- `tradetalk_service_action_review_prompt`

---

## Freshness and automation

| Layer | Mechanism |
|-------|-----------|
| Live repo | Tools read files at request time under `TRADETALK_ROOT`. |
| Index | [`scripts/generate_context_index.py`](../scripts/generate_context_index.py) writes [`.tradetalk/context-index.json`](../.tradetalk/context-index.json). |
| OpenAPI | `fetch_openapi_json` when the API is up. |

**Pre-commit:** [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) regenerates the context index when backend/frontend/docs change.

**CI:** [`.github/workflows/mcp-context-freshness.yml`](../.github/workflows/mcp-context-freshness.yml) fails if the committed index is stale, validates the action registry, and runs MCP unit tests.

---

## Testing and validation

From the repo root (after `pip install -r requirements-mcp.txt` in `.venv-mcp`):

```bash
PYTHONPATH=. python3 -m unittest discover -s tradetalk_mcp/tests -v
python3 scripts/validate_mcp_actions.py
python3 scripts/generate_context_index.py
git diff --exit-code .tradetalk/context-index.json
```

**Maintainer validation (2026-05-09):** All 10 context tools register in `TRADETALK_MCP_MODE=context`; 16 tools in `full` mode including `health_check_backend` and `trigger_approved_action`; 10 unit tests pass; registry validation and context-index freshness check pass.

Optional: [MCP Inspector](https://github.com/modelcontextprotocol/inspector) for interactive protocol debugging.

---

## Security notes

- Path reads are confined to `TRADETALK_ROOT`; `..` and absolute paths are rejected.
- HTTP clients only use hosts in the allowlist (from base URL or `TRADETALK_API_HOST_ALLOWLIST`).
- No shell execution; no generic “call any URL” tool.

---

## Recommended agent preamble

Before editing TradeTalk, an agent should call **`get_mcp_status`**, read the context index and **`docs/ARCHITECTURE.md`**, list **`backend/routers`**, and optionally fetch OpenAPI. Do not invoke mutating service tools unless the user asks and **`TRADETALK_MCP_ACTIONS_ENABLED=true`**.
