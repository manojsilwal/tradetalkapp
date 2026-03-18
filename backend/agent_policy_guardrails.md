# Agent Policy Guardrails

In-process defense-in-depth policy layer in `backend/agent_policy_guardrails.py`.

## IMPORTANT: Security Model Limitation

This is an **in-process** Python policy engine. It provides deterministic
capability checks and host allowlists as a first line of defense, but it
**cannot prevent bypass by a compromised process**.

For real OS/container-level isolation, migrate to NVIDIA OpenShell when it
exits alpha. OpenShell enforces policies out-of-process at the kernel level.

## What It Enforces

- Per-workload capability checks (least privilege).
- Outbound host allowlists for LLM and data egress.
- Startup secret validation with optional strict fail-fast mode.
- Secret redaction helper for runtime error logging.

## Workload Profiles

| Workload       | Capabilities                                                    |
|----------------|-----------------------------------------------------------------|
| `debate`       | `knowledge_read`, `knowledge_write`, `llm_inference`            |
| `backtest`     | `knowledge_read`, `knowledge_write`, `llm_inference`, `market_data_read` |
| `notifications`| `news_ingest`, `knowledge_write`, `notifications_emit`          |
| `scheduler`    | `news_ingest`, `market_data_read`, `knowledge_write`            |
| `video`        | `llm_inference`, `video_generation`                             |
| `llm`          | `llm_inference`                                                 |

## Env Flags

- `GUARDRAILS_ENABLE=1`: turns policy checks on (default: enabled).
- `GUARDRAILS_ALLOWED_HOSTS`: optional comma-separated additional hosts.
- `GUARDRAILS_STRICT_STARTUP=1`: fail startup on missing required secrets.

## Inference Architecture

Inference goes **direct to OpenRouter** — never proxied through HF Space.
The HF Space runs an OpenClaw agent runtime and is NOT an inference endpoint.

```
FastAPI Backend (Render) ---> OpenRouter API (direct)
                                 |
                              Nemotron Super (free)
```

## API Checks

- `GET /llm/status` shows active backend/provider/model/endpoint.
- `GET /runtime/policy-check` runs a capability-block self-test and returns startup secret issues.
