# Phase B — Evidence memo export + dreaming job

## Evidence memo (chat)

After each completed assistant turn, the server stores the last user message, assistant text, evidence contract, and chat meta on the in-memory session. **Export** builds a frozen Markdown memo (Decision Terminal–style disclaimer + provenance table).

- Builder: [`backend/evidence_pack.py`](../backend/evidence_pack.py)
- API: `POST /chat/evidence-export` with `{ "session_id": "..." }` → `{ markdown, generated_at_utc, schema_version }`
- UI: Chat page **Export memo** downloads `tradetalk-evidence-<prefix>.md` (enabled after at least one turn with an evidence contract)

## Handoff events (D / E)

Swarm trace and debate completions append rows to `coral_handoff_events` (SQLite, same DB as CORAL — migration `002_handoff_events.sql`).

| event_type | Source |
|------------|--------|
| `handoff_swarm_trace` | After `/trace` / swarm consensus |
| `handoff_debate` | After debate completes |

Helpers: [`backend/coral_hub.py`](../backend/coral_hub.py) — `log_handoff_event`, `list_handoff_events_since`.

## Dreaming job

[`backend/coral_dreaming.py`](../backend/coral_dreaming.py) aggregates recent events into one CORAL note (and a small skill with tickers). Scheduled **daily at 01:40 UTC** via [`backend/daily_pipeline.py`](../backend/daily_pipeline.py).

| Env | Default | Purpose |
|-----|---------|---------|
| `CORAL_DREAMING_ENABLED` | `1` | Disable with `0` |
| `CORAL_DREAMING_HOURS` | `24` | Lookback window |
| `CORAL_DREAM_NOTE_TTL_SEC` | 14d | Dream note TTL |

## Tests

```bash
PYTHONPATH=. python -m unittest backend.tests.test_evidence_pack -v
PYTHONPATH=. python -m unittest backend.tests.test_coral_hub.TestCoralHub.test_handoff_events_roundtrip -v
```
