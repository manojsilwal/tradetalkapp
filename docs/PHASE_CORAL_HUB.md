# CORAL hub phase (multi-agent wiring)

Extends the existing SQLite CORAL hub ([`backend/coral_hub.py`](../backend/coral_hub.py)) with **named finance agents** and **per-agent heartbeat notes**.

## Four agents

| Agent id | Role | Observation source (v1) |
|----------|------|---------------------------|
| `data_ingest` | MIL / pipeline | Headline count, sector_perf keys, cache age |
| `technical` | L1 / structure | SPY, QQQ, GLD, VIX, credit stress, sector ETF count |
| `sentiment` | Headlines | First 3 MIL headlines |
| `gold_analysis` | Gold / USD | GLD and UUP from L1 (full Gold Advisor remains `/advisor/gold`) |

## Python API (MCP-ready surface)

[`backend/coral_agents.py`](../backend/coral_agents.py) exposes:

- `hub_add_note`, `hub_add_skill`, `hub_record_attempt` — thin wrappers with optional warnings for unknown `agent_id`
- `CORAL_TOOL_DESCRIPTORS` — manifest-style list for future MCP export

Swarm trace attempts use `hub_record_attempt` from [`backend/routers/analysis.py`](../backend/routers/analysis.py).

## Scheduler

[`backend/daily_pipeline.py`](../backend/daily_pipeline.py) runs every `CORAL_HEARTBEAT_MINUTES` (default **30**):

1. `run_coral_heartbeat` — legacy `heartbeat` agent note (intel one-liner + peers)
2. `run_coral_agent_reflections` — **four** notes (`data_ingest`, `technical`, `sentiment`, `gold_analysis`)

Both respect US equity **regular** hours unless `CORAL_HEARTBEAT_IGNORE_MARKET_HOURS=1` (dev/tests).

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `CORAL_AGENT_REFLECTIONS` | `1` | Set `0` to disable the four per-agent notes (heartbeat unchanged) |
| `CORAL_HEARTBEAT_ENABLED` | `1` | Disable all heartbeat writes |
| `CORAL_HEARTBEAT_IGNORE_MARKET_HOURS` | `0` | `1` = run outside 09:30–16:00 ET |
| `CORAL_HEARTBEAT_MINUTES` | `30` | APScheduler interval (minimum 5 in code) |
| `CORAL_NOTE_TTL_SEC` | 7d | Note expiry |

See [`backend/.env.example`](../backend/.env.example) (CORAL section).

## Tests

```bash
PYTHONPATH=. python -m unittest backend.tests.test_coral_hub.TestCoralAgentReflections -v
```

## Next roadmap slice

Phase **9–12** (evidence export + dreaming job) builds on this hub for skills/notes extraction from traces — see project plan.
