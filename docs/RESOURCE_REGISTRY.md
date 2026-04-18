# Resource Registry (RSPL — Phase A)

> Phase A of TradeTalk's incremental adoption of the Autogenesis Protocol
> ([arXiv:2604.15034v1](https://arxiv.org/abs/2604.15034)). Introduces a
> protocol-registered, versioned substrate for LLM prompts so every
> inference call and reflection can be traced to the exact prompt that
> produced it.

## TL;DR

| What                                | Where                                              |
|-------------------------------------|----------------------------------------------------|
| Prompt bodies (source of truth)     | `backend/resources/prompts/*.yaml`                 |
| Versioned storage (SQLite)          | `backend/resources.db` — `RESOURCES_DB_PATH`       |
| Schema                              | `backend/migrations/resources/001_initial_schema.sql` |
| Registry API                        | `backend/resource_registry.py`                     |
| Seeder                              | `backend/resource_seeder.py`                       |
| Read-only HTTP surface              | `GET /resources/*`                                 |
| Feature flag (fallback on failure)  | `RESOURCES_USE_REGISTRY=1` (default)               |

All consumers today fall back **byte-exactly** to `AGENT_SYSTEM_PROMPTS` in
`backend/llm_client.py` if the registry is unavailable. Pre-registry
behavior is the proven path of last resort.

## Why (the problem this solves)

Before Phase A, every system prompt lived as an inline string in
`backend/llm_client.py` or `backend/agents.py`. Consequences:

1. **No lineage.** When a swarm reflection fired, there was no way to
   associate the lesson with the exact wording of the prompts that
   produced it.
2. **No safe mutation.** Editing a prompt in place silently changed every
   subsequent call with no rollback path.
3. **No per-resource policy.** Prompts that drive user-facing numerical
   surfaces (e.g. `decision_terminal_roadmap`) had the same
   "edit-at-will" posture as exploratory analysts.

AGP §3.1.2 prescribes a protocol-registered resource substrate with
explicit version, lineage, and a `learnability` bit per resource. Phase A
implements this slice for `PROMPT` resources only — future phases will
register `AGENT`, `TOOL`, `ENV`, and `MEM` resources without a schema
migration.

## Data model

Three SQLite tables (backend/resources.db):

### `resource_records` — the versioned content

One row per `(name, version)`. Insert-only (updates never overwrite).

| column          | purpose                                       |
|-----------------|-----------------------------------------------|
| `name`          | e.g. `bull`, `moderator`, `gold_advisor`      |
| `kind`          | `prompt` (agent/tool/env/mem reserved)        |
| `version`       | semver string, e.g. `1.0.0`                   |
| `description`   | one-line human summary                        |
| `learnable`     | 0/1 — whether SEPL may ever update this row   |
| `body`          | the actual prompt text sent to the LLM        |
| `schema_json`   | optional LLM-facing output schema             |
| `fallback_json` | fallback JSON for when the LLM fails          |
| `metadata_json` | free-form operator metadata                   |
| `source_path`   | which YAML seeded the row (for audit)         |
| `created_at`    | unix timestamp                                |

### `resource_active` — the live pointer

One row per `name`, pointing at which `(name, version)` is currently
served. Callers always read this unless they ask for an explicit version.

### `resource_lineage` — the audit trail

One row per register/update/restore operation. Includes `reason` (freeform
text) and `actor` (e.g. `seed:yaml`, `human:<id>`, `sepl:<run_id>`). All
mutations flow through `ResourceRegistry.update()` / `.restore()`, which
refuse to run without both.

## Lifecycle

```
[YAML at backend/resources/prompts/*.yaml]
              │
              ▼ (on startup)
   resource_seeder.seed_on_startup()
              │ idempotent — never overwrites existing rows
              ▼
     [resource_records table]
              │
              ▼ (every LLM call)
   LLMClient._resolve_system_prompt(role)
              │ reads active pointer → falls back to AGENT_SYSTEM_PROMPTS
              │                         if registry unavailable
              ▼
     [system prompt sent to LLM]
              │
              ▼ (swarm or reflection write)
   knowledge_store.add_swarm_{analysis,reflection}(..., prompt_versions={...})
              │
              ▼
   [Chroma/Supabase vector memory, with lineage stamped on every row]
```

## Managing prompts

### Adding a new prompt

1. Create `backend/resources/prompts/<name>.yaml`. Required keys:
   `name`, `kind: prompt`, `version`, `body`. Optional: `description`,
   `learnable` (default `false`), `schema`, `fallback`, `metadata`.
2. Restart the service. The seeder inserts the new row and promotes it
   active (because there was no prior active pointer).
3. Verify via `GET /resources/<name>`.

### Editing an existing prompt (human-reviewed)

Two equally valid paths:

#### Path A — via API (Phase A: read-only; real use in Phase B)

Not yet exposed. In Phase A all updates go through code.

#### Path B — via a short ops script

```python
from backend.deps import resource_registry
resource_registry.update(
    name="bull",
    new_body="...better prompt...",
    bump="minor",                          # patch | minor | major
    reason="tightened JSON-only instruction",
    actor="human:manoj",
)
```

This:

* Rejects with `ResourcePinnedError` if the resource has `learnable=False`.
* Creates a new row in `resource_records` at the bumped semver.
* Flips `resource_active` to the new version.
* Appends an entry to `resource_lineage`.

Bumping the YAML file's `version:` field after a human update is
optional — the seeder treats the DB as source of truth and **never**
overwrites an operator update (see `test_yaml_does_not_overwrite_human_update`).

### Rolling back

```python
resource_registry.restore(
    name="bull",
    version="1.0.0",
    reason="v1.1 regressed debate quality on small caps",
    actor="human:manoj",
)
```

No new row is created — the active pointer simply flips back. The attempt
is recorded in lineage with `operation="restore"`.

## Learnability policy (Phase A)

**Pinned (`learnable: false`) — must stay stable**

These produce user-visible verdicts, numerical projections, or executable
rules. Mutating them requires a code review, not an autonomous optimizer.

- `moderator` — final investment-committee verdict
- `swarm_synthesizer` — final swarm-consensus verdict
- `gold_advisor` — regulated-adjacent (investment advice boundary)
- `strategy_parser` — parses English → executable backtest rules
- `backtest_explainer` — investor-facing educational copy
- `decision_terminal_roadmap` — USD price scenarios on Decision Terminal

**Learnable (`learnable: true`) — candidate for future SEPL optimization**

These are internal reasoning surfaces where a disagreement between two
versions is recoverable:

- `bull`, `bear`, `macro`, `value`, `momentum` — debate-panel analysts
- `swarm_analyst` — per-factor ambiguous-zone reasoning
- `swarm_reflection_writer` — writes post-trade lessons (feedback loop)
- `video_scene_director`, `video_veo_text_fallback` — academy clip planning
- `rag_narrative_polish` — placeholder for a later narrative pass
- `sitg_scorer` — Skin-In-The-Game (Risk-Return-Ratio Step 2e) scorer
- `execution_risk_scorer` — qualitative execution risk (Step 2c) scorer
- `scorecard_verdict` — 1-sentence per-ticker Scorecard narrative

### Risk-Return-Ratio personas (Scorecard feature)

Three purpose-built personas back the `/scorecard` endpoints. Each follows
the same register/version/lineage contract as the debate personas; the
deterministic math (normalization, PE-stretch, weighted averages, quadrant)
lives in `backend/scorecard.py` and is exercised by
`backend/tests/test_scorecard_math.py` — the LLM is only used for the
qualitative, judgment-heavy factors below.

| prompt            | step | output fields                                                                                                        | tier  | fixture                                          |
|-------------------|------|----------------------------------------------------------------------------------------------------------------------|-------|--------------------------------------------------|
| `sitg_scorer`     | 2e   | `sitg_score` 0-10, `ceo_name`, `ownership_pct`, `form4_buys_12m`, `form4_sells_12m`, `compensation_mix`, `archetype`, `reasoning` | heavy | `backend/resources/sepl_eval_fixtures/sitg_scorer.json` |
| `execution_risk_scorer` | 2c | `exec_score` 1-10, `profile_tier` (utility / industrial / high-growth / turnaround), `reasoning`                | heavy | _(deferred; covered by router tests w/ fakes)_   |
| `scorecard_verdict`     | 8  | `verdict` (enum: Strong / Favorable / Balanced / Stretched / Avoid), `one_line_reason`                          | light | _(deferred)_                                    |

Schema-conformance tests for `sitg_scorer` live in
`backend/tests/test_sitg_prompt.py` — they validate the YAML parses,
the declared JSON schema is a valid Draft-7 schema, the fallback object
itself conforms to that schema (so outage behavior is guaranteed), and
the eval fixture exercises at least one founder-operator and one
hired-CEO case that the Step 2e rubric specifically calls out.

Phase A never mutates `learnable=True` rows automatically — the bit
simply encodes permission so Phase B's SEPL operator knows where it is
allowed to run.

## Outage safety

`LLMClient._resolve_system_prompt(role)` is explicitly defensive:

1. If `RESOURCES_USE_REGISTRY=0` → skip the registry, use `AGENT_SYSTEM_PROMPTS`.
2. If the registry import / query raises → same fallback.
3. If the queried record has an empty body → same fallback.

Version returned to callers in each fallback is the string `"unversioned"`
so reflections written during an outage are still inspectable.

Tests covering these paths:

- `backend/tests/test_llm_client_dual_read.py::TestRegistryOutageSafe`
- `backend/tests/test_llm_client_dual_read.py::TestResolveSystemPrompt::test_flag_off_uses_legacy_dict`

## HTTP surface (read-only in Phase A)

| endpoint                                   | returns                                       |
|--------------------------------------------|-----------------------------------------------|
| `GET /resources/summary`                   | count + snapshot_id + db_path                 |
| `GET /resources/?kind=prompt`              | list of active records                        |
| `GET /resources/{name}`                    | full record (body + schema + fallback)        |
| `GET /resources/{name}/versions`           | all versions newest-first                     |
| `GET /resources/{name}/lineage?limit=50`   | audit trail (register/update/restore)         |

There is intentionally **no** PATCH/POST/DELETE in Phase A. Mutations go
through code paths with human review. Phase B will add SEPL-governed
update endpoints behind `GUARDRAILS_ENABLE`.

## Lineage on reflections

Every `knowledge_store.add_swarm_analysis` and `add_swarm_reflection`
call stamps three fields on the stored metadata:

- `prompt_versions` — JSON-encoded `{role: semver}` dict
- `agent_version`   — "unversioned" in Phase A (reserved for AGENT kind)
- `registry_snapshot_id` — 16-char sha256 prefix of the set of active versions

The `analysis.py` `/trace` handler captures these at swarm-run time.
`daily_pipeline._track_swarm_outcomes` propagates the original run's
versions forward onto the reflection row, so when a lesson fires two days
later it is attributed to the prompts that originally produced the
prediction, not to whatever is active on the day the reflection runs.

## Feature flags

| env var                    | default | effect when unset/`1`                 | effect when `0`                        |
|----------------------------|---------|---------------------------------------|----------------------------------------|
| `RESOURCES_USE_REGISTRY`   | `1`     | read prompts from SQLite              | always use hardcoded `AGENT_SYSTEM_PROMPTS` |
| `RESOURCES_AUTOSEED`       | `1`     | seed from YAML on startup             | do not import YAML at startup          |
| `RESOURCES_DB_PATH`        | unset   | defaults to `backend/resources.db`    | absolute or repo-relative override     |

## Tests

42 tests across four files cover Phase A end-to-end:

| file                                                         | count |
|--------------------------------------------------------------|-------|
| `backend/tests/test_resource_registry.py`                    | 26    |
| `backend/tests/test_llm_client_dual_read.py`                 | 9     |
| `backend/tests/test_resources_router.py`                     | 10    |
| `backend/tests/test_prompt_lineage_in_reflections.py`        | 7     |

Run the phase-A slice with:

```bash
pytest backend/tests/test_resource_registry.py \
       backend/tests/test_llm_client_dual_read.py \
       backend/tests/test_resources_router.py \
       backend/tests/test_prompt_lineage_in_reflections.py -v
```

## What comes next (Phase B preview — not in this PR)

Phase B wires the SEPL operators (Reflect, Select, Improve, Evaluate,
Commit) on top of this substrate:

1. **Reflect (ρ)** — reuse the existing `swarm_reflection_writer` output,
   now with `prompt_versions` stamped.
2. **Select (σ)** — periodic job picks learnable prompts whose active
   version correlates with recent effectiveness scores below some
   threshold.
3. **Improve (ι)** — an optimizer prompt proposes a new `body` for the
   selected prompt.
4. **Evaluate (ε)** — candidate runs against a held-out eval set of
   prior swarm analyses with known outcomes.
5. **Commit (κ)** — if candidate wins on eval, `resource_registry.update`
   promotes it; otherwise the attempt is discarded and logged to
   lineage with `actor=sepl:<run_id>` and `operation=proposal`.

None of these mutate Phase A semantics. If Phase B is never shipped, the
Phase A infrastructure keeps earning its cost by making every prompt
change human-reviewable and reversible.
