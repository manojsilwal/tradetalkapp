# Tool Evolution — SEPL-for-TOOLs (Phase C1 + C2)

> Operational & architectural reference for evolving the **numeric
> configuration** of registered tier-0/1 tools. This is the sister document
> to [`docs/SEPL.md`](SEPL.md) (prompt evolution) and builds on
> [`docs/RESOURCE_REGISTRY.md`](RESOURCE_REGISTRY.md) (Phase A substrate).
> See the source paper _Autogenesis: A Self-Evolving Agent Protocol_
> (arXiv:2604.15034v1) §3.2 for the theoretical framing.

## 1. Why tool evolution is a distinct phase

Phase B (SEPL for prompts) closes the loop on how the swarm _talks_. But most
of the swarm's quantitative decisions — "is SIR > 15 a bullish squeeze?",
"does VIX=18 constitute macro stress?", "which P/E deserves a bearish
stance?" — live inside hardcoded numeric thresholds. Those thresholds are
the single biggest source of un-audited magic numbers in the codebase.
Phase C evolves them the same way Phase B evolves prompts, with a much
smaller risk surface:

- Prompt evolution relies on an LLM to draft candidates. Tool evolution
  does **not**: `Improve` is a bounded random walk within ranges the YAML
  itself declares, so prompt-injection and unbounded drift are structurally
  impossible.
- Prompt evaluation reads live reflection metadata. Tool evaluation is
  **100% offline** — it scores against held-out JSON fixtures and never
  touches live connectors, LLMs, or traffic.
- Prompts are free-form strings. Tool configs are a flat `{key: number}`
  map with a JSON Schema, so `update_tool_config` can refuse unknown keys
  outright.

Everything is **off by default** and gated behind an independent feature
flag. Nothing runs until an operator turns it on.

## 2. Surface area

| File | Responsibility |
| --- | --- |
| `backend/resources/tools/*.yaml` | Canonical tool definitions: `config` (defaults), `fallback` (identical for dual-read), `parameter_ranges`, `schema`, `tier`, `learnable`. |
| `backend/tool_handlers.py` | Pure, deterministic Python functions — one per tool. Called from both the live code path and the shadow evaluator. No I/O, no logging, no exceptions on bad input. |
| `backend/tool_configs.py` | Dual-read helper `get_tool_config(name, default)` and SEPL-facing writer `update_tool_config(name, cfg, reason, actor)`. |
| `backend/resources/sepl_eval_fixtures_tools/*.json` | Held-out fixtures: `{"input": {...}, "expected": <primitive>, "weight": <float>}`. Scored active-vs-candidate by the Evaluate operator. |
| `backend/sepl_tool.py` | The five operators (Select, Improve, Evaluate, Commit), the run-cycle orchestrator, the tier-aware budget gate, and `SEPLToolKillSwitch` for post-commit auto-rollback. |

## 3. The four operators

Autogenesis §3.2 specifies a closed-loop operator algebra. The tool variant
drops "Reflect" because there's no natural `Reflection` object for tools —
every decision is a deterministic function of inputs, so the fixture set
**is** the reflection corpus. The four remaining operators are:

| Operator | Code | What it does |
| --- | --- | --- |
| Select | `SEPLTool.select` | Pick a single **learnable** TOOL to evolve this cycle. Today this is least-recently-updated; SEPL does not retry a tool it just committed. |
| Improve | `SEPLTool.improve` | Emit up to `SEPL_TOOL_CANDIDATES_PER_CYCLE` candidate configs by bounded numeric perturbation. For each candidate and each key, pick a step count in `[-SEPL_TOOL_MAX_PERTURB_STEPS, +SEPL_TOOL_MAX_PERTURB_STEPS]` uniformly, multiply by the key's declared `step`, add to the current value, and clamp into the `[min, max]` range. Never invents keys. Never calls an LLM. |
| Evaluate | `SEPLTool.evaluate` | Score the **active** config and a candidate against the tool's fixture JSON. Returns a `ToolEvalResult` with active/candidate hit-rates and their weighted-margin. |
| Commit | `SEPLTool.commit` | Refuse when `margin < SEPL_TOOL_MIN_MARGIN`, when the tool is `learnable=False`, when the effective daily cap is exhausted, or when the registry is down. Otherwise calls `update_tool_config(name, cfg, actor="sepl:tool", ...)` — which writes a new version row, updates the active pointer, and stamps the change in lineage. |

`SEPLTool.run_cycle(tool_names)` chains them in order and returns a
`ToolCycleReport` describing exactly which gate fired or which version was
committed.

## 4. Dual-read contract

Every production call site of a tool config goes through

```python
from backend.tool_configs import get_tool_config

cfg = get_tool_config("short_interest_classifier", _HARDCODED_DEFAULTS)
```

The helper is a strict three-step fallback:

1. If `RESOURCES_USE_REGISTRY=0`, return the hardcoded default unchanged.
2. If the registry is up and the record exists, merge the record's
   `metadata.config` into the default (default keys provide lower bounds
   when the record is partial).
3. On any exception — DB corruption, schema mismatch, missing tool — log
   and return the default.

Because the default dict is the ground truth, **the pre-evolution behaviour
is always recoverable by flipping `RESOURCES_USE_REGISTRY=0`**. The
canonical configs shipped in `backend/resources/tools/*.yaml` are chosen
so the flag-on and flag-off paths produce byte-identical outputs on the
fixture set — verified by `backend/tests/test_tool_dual_read_integration.py`
and `backend/tests/test_macro_tool_integration.py::TestMacroConnectorFlagOff`.

## 5. Tool tiers and the budget gate

Every TOOL YAML declares a `tier` in `metadata`. The tier expresses the
worst-case blast radius of a bad config:

| Tier | Semantic | SEPL permissions |
| --- | --- | --- |
| 0 | Pure, internal, deterministic. No I/O. | Evolvable. Default cap: **2 commits / 24h**. |
| 1 | External read-only (e.g. yfinance). No writes. | Evolvable. Default cap: **1 commit / 24h**. |
| 2 | External with cost, quota, or writes. | **Blocked**. Cap defaults to `0`. |
| 3 | Critical / irreversible (e.g. account-altering). | **Blocked**. Cap defaults to `0`. |

The commit path enforces the **stricter** of the global
`SEPL_TOOL_MAX_PER_DAY` and the per-tier
`SEPL_TOOL_MAX_PER_DAY_TIER_<N>`. This means tier-2+ tools can never be
committed by SEPL, **even if an operator accidentally raises the global
cap**, because the tier cap is still 0. Overriding a tier cap is an
explicit single-env-var decision — verified by
`backend/tests/test_macro_tool_integration.py::TestTierGateEnforcedInCommit`.

## 6. Kill switch (fixture-based auto-rollback)

`SEPLToolKillSwitch.check(tool_name)` implements an offline regression
detector for SEPL commits:

1. Walk the tool's lineage backwards. Find the most recent event whose
   `actor == "sepl:tool"`, `operation == "update"`, and `created_at` is
   within `SEPL_TOOL_ROLLBACK_WINDOW_HOURS` (default 7 days).
2. Drop that event if a later `operation == "restore"` with an actor
   `"sepl:tool:rollback:*"` already reverted it — this prevents double
   rollbacks on repeated `check()` calls.
3. Load both the committed version's config and its `from_version`
   config.
4. Score both against the tool's fixture file.
5. If `prior_score - committed_score >= SEPL_TOOL_ROLLBACK_MARGIN`:
   - When `SEPL_TOOL_AUTOCOMMIT=0` (default): report `DRY_RUN` only.
   - When `SEPL_TOOL_AUTOCOMMIT=1`: call `registry.restore(name, prior_version, actor="sepl:tool:rollback:<run_id>")`.

Three invariants make this safe:

- Manual / human commits are **ignored** (actor filter).
- Missing fixtures / handlers surface as typed outcomes (`NO_FIXTURES`,
  `NO_HANDLER`), never silent failures.
- The rollback `restore` uses `operation="restore"`, which is filtered
  out of `_recent_commits` in the commit-rate-limit check, so a rollback
  never consumes SEPL's daily commit budget.

## 7. Feature-flag matrix

| Variable | Default | Role |
| --- | --- | --- |
| `SEPL_TOOL_ENABLE` | `0` | Master switch. `run_cycle` aborts as `ABORTED_DISABLED` when off. |
| `SEPL_TOOL_DRY_RUN` | `1` | Stop before Commit even when enabled. |
| `SEPL_TOOL_MIN_MARGIN` | `0.05` | Candidate must beat active by ≥5 pp on fixtures. |
| `SEPL_TOOL_MAX_PER_DAY` | `2` | Global per-tool commit cap. |
| `SEPL_TOOL_MAX_PER_DAY_TIER_0` | `2` | Tier-0 cap. |
| `SEPL_TOOL_MAX_PER_DAY_TIER_1` | `1` | Tier-1 cap. |
| `SEPL_TOOL_MAX_PER_DAY_TIER_2` | `0` | Tier-2 cap (blocks SEPL). |
| `SEPL_TOOL_MAX_PER_DAY_TIER_3` | `0` | Tier-3 cap (blocks SEPL). |
| `SEPL_TOOL_MAX_PERTURB_STEPS` | `4` | Per-key, per-call step bound for Improve. |
| `SEPL_TOOL_CANDIDATES_PER_CYCLE` | `4` | Max candidates Improve proposes per cycle. |
| `SEPL_TOOL_SEED` | _unset_ | Deterministic RNG seed for tests. |
| `SEPL_TOOL_AUTOCOMMIT` | `0` | Kill-switch master. Off = report only. |
| `SEPL_TOOL_ROLLBACK_MARGIN` | `0.05` | Prior must beat committed by this fraction to trigger rollback. |
| `SEPL_TOOL_ROLLBACK_WINDOW_HOURS` | `168` | Only inspect SEPL commits inside this window. |

## 8. Shipped tools

| Tool | Tier | Knobs | Consumer |
| --- | --- | --- | --- |
| `short_interest_classifier` | 0 | `sir_bull_threshold`, `sir_ambiguous_min`, `sir_ambiguous_max`, `dtc_confirm_threshold`, `bearish_csi_threshold` | `backend/agents.py::ShortInterestAgentPair` |
| `debate_stance_heuristic_bull` | 0 | `sir_bull_floor`, `rev_growth_bull_floor`, `r3m_bull_floor`, `sir_bear_ceiling`, `rev_growth_bear_ceiling`, `r3m_bear_ceiling` | `backend/debate_agents.py::_determine_stance` (bull) |
| `debate_stance_heuristic_bear` | 0 | `pe_bear_threshold`, `debt_eq_bear_threshold`, `r3m_bear_ceiling`, `pe_bull_ceiling`, `r3m_bull_floor` | `backend/debate_agents.py::_determine_stance` (bear) |
| `macro_vix_to_credit_stress` | 1 | `divisor`, `status_threshold` | `backend/connectors/macro.py::MacroHealthConnector` |

## 9. Adding a new tool

1. Write a pure handler in `backend/tool_handlers.py`. Never read env
   vars, the registry, or the clock. Never raise on missing keys —
   return a well-defined default primitive.
2. Add it to `TOOL_HANDLERS` with the `output_kind` it returns.
3. Create `backend/resources/tools/<name>.yaml`:
   - `tier` must be 0 or 1 today; tier-2+ requires new safety review.
   - `metadata.config` and `fallback` must have **identical keys** and
     values (dual-read invariant).
   - `metadata.parameter_ranges` must declare `{min, max, step}` for
     every key you want SEPL to touch. Omitting a key pins it.
   - `schema` must validate all keys in `config`.
4. Refactor the production call site to use `get_tool_config(name, HARDCODED_DEFAULT)`.
   The hardcoded default must equal the YAML `fallback` byte-for-byte.
5. Add fixtures at
   `backend/resources/sepl_eval_fixtures_tools/<name>.json`. Cover
   decision-boundary cases. The canonical config **must** score 100%
   on its own fixtures — add a self-check test.
6. Add tests:
   - Pure handler tests (`tests/test_tool_handlers.py`).
   - Dual-read integration (`tests/test_tool_dual_read_integration.py`
     or a new file).
   - Extend the real-tool SEPL canary
     (`tests/test_sepl_tool.py::TestRunCycleOnRealTools`) to include
     the new name.

## 10. Test surface

Phase C ships with ~125 tool-specific tests. Run them in isolation with:

```bash
pytest backend/tests/test_tool_resources.py \
       backend/tests/test_tool_handlers.py \
       backend/tests/test_tool_dual_read_integration.py \
       backend/tests/test_sepl_tool.py \
       backend/tests/test_macro_tool_integration.py -q
```

The full backend suite (360+ tests) must always stay green after any
tool-evolution change.
