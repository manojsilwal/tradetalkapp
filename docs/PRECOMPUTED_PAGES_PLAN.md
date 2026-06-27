# Plan: precompute Picks & Shovels, Narrative Rotation Radar, and Fund Leaderboard via daily cron

**Goal.** The three "global" pages — **Picks & Shovels Momentum Finder** (`/picks-shovels`), **Narrative Rotation Radar** (`/narrative-radar`), and **Fund Leaderboard** (`/intelligence/funds/leaderboard`) — should load **instantly from precomputed, shared, daily-refreshed data**, instead of showing a blank "click Run/Refresh" state and forcing the user to wait 1–2 minutes for a live scan. These datasets are the same for every user and change at most daily, so they are a perfect fit for a scheduled warm + durable snapshot.

> **Status:** Implemented. Durable shared store `backend/durable_snapshot.py` (Postgres dual-write, degrades to local SQLite / inactive when unconfigured) wired into `picks_shovels/store.py` + `narrative_radar/store.py` (snapshots + alerts survive Cloud Run cold starts). Synchronous warm endpoints `POST /knowledge/picks-shovels-run` + `POST /knowledge/narrative-radar-run` (added/updated). External cron `.github/workflows/precompute-pages.yml` (daily P&S + Radar, weekly leaderboard) + secondary in-process APScheduler jobs (`picks_shovels_daily` 00:40, `narrative_radar_daily` 00:50). Frontend cold-start self-warm on `/picks-shovels` and `/narrative-radar`. Tests: `backend/tests/test_narrative_radar.py` (57, incl. durable round-trip + cold-start survival). The Fund Leaderboard was already Postgres-durable; the weekly cron triggers its ingest.

---

## 1. Why the pages are blank / slow today (root cause)

This is **not** simply "no cron exists." There are two compounding architectural causes, confirmed in [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md):

1. **Ephemeral filesystem on Cloud Run.** Picks & Shovels and Narrative Radar persist their snapshots in **local SQLite** files (`backend/picks_shovels/store.py` → `picks_shovels.db`; `backend/narrative_radar/store.py` → `narrative_radar.db`, both under `TRADETALK_DATA_DIR` or the backend dir). Cloud Run's container filesystem is **ephemeral and per-instance** (ARCHITECTURE §10/§"Render's filesystem is ephemeral"). So a snapshot written by a user-triggered scan **disappears on the next cold start / new instance**, and the page falls back to the blank "trigger a refresh" state. The leaderboard does **not** have this problem because its store (`backend/fund_leaderboard_store.py`) is **Postgres-capable** (`_use_postgres()`), i.e. durable.

2. **Scale-to-zero kills in-process schedulers.** ARCHITECTURE §10.x: *"Cloud Run scale-to-zero: the service can scale to 0 instances; while at 0, no in-process schedulers run."* So the APScheduler jobs in `backend/daily_pipeline.py` / `backend/main.py` (including the `narrative_radar_daily` 00:50 UTC job and the `FUND_LB_SCHEDULE_ENABLE` weekly job) **do not reliably fire**. The repo's documented durable mechanism is **external cron (GitHub Actions) → secured HTTP endpoints** (e.g. `POST /knowledge/pipeline-run`, see [`docs/CRON.md`](./CRON.md)).

**Current per-page reality:**

| Page | Read path | Write/warm path | Durable? | Symptom |
|---|---|---|---|---|
| Picks & Shovels | `GET /picks-shovels/stocks` serves latest SQLite snapshot; else "trigger a refresh" | **only** manual `POST /picks-shovels/refresh` (no scheduler job at all) | ❌ local SQLite | blank after cold start; user must run a 1–2 min scan |
| Narrative Radar | `GET /narrative-radar/overview` serves latest SQLite snapshot; else "click Refresh" | manual `POST /narrative-radar/refresh` + in-process daily job (unreliable at scale-zero) + `POST /knowledge/narrative-radar-run` cron endpoint | ❌ local SQLite | same |
| Fund Leaderboard | `GET /api/funds/leaderboard` (DB-backed, instant) | weekly ingest gated by `FUND_LB_SCHEDULE_ENABLE=0` (off) | ✅ Postgres | empty until ingest runs once; ingest is heavy/manual |

**Conclusion:** the fix is two pillars — **(A) durable, shared snapshot storage** so a precomputed snapshot survives cold starts and is readable by any instance, and **(B) external daily cron** that warms all three (because in-process schedulers don't run at zero) — plus **(C) a cache-first frontend** that always renders the latest snapshot instantly and never forces a manual run.

---

## 2. Design principles

1. **Precompute once, serve many.** One shared snapshot per page per day; never per-user, never on the request hot path.
2. **Durable + instance-independent.** Snapshots must survive Cloud Run cold starts and be readable by any instance → not local SQLite in production.
3. **External cron is the source of truth for scheduling.** In-process APScheduler stays as a best-effort secondary for always-on / local deploys.
4. **Stale-while-revalidate.** Always serve the latest snapshot instantly; if it is older than its TTL, kick a **non-blocking background** refresh. Never block a page load on a scan.
5. **Warm must complete server-side.** Because background tasks can be killed when the instance scales to zero after responding, the warm runs **synchronously within the cron request** (Cloud Run keeps the instance alive for the request duration) **or** as a **Cloud Run Job**. Do not rely on fire-and-forget background tasks for the cron warm.
6. **Reuse, don't rebuild.** Reuse the existing engines (`picks_shovels.engine.run_scan`, `narrative_radar.engine.run_scan`, `fund_leaderboard_job.run_fund_leaderboard_job`), the `postgres_enabled()` dual-write pattern, the cron-secret guard (`backend/cron_auth.py`), and the existing GitHub Actions cron pattern (`.github/workflows/render-daily-pipeline.yml`).

---

## 3. Pillar A — durable, shared snapshot storage

The leaderboard is already durable (Postgres). The work is to make **Picks & Shovels** and **Narrative Radar** snapshots durable and instance-independent. Choose one backend (recommend **Postgres dual-write**, matching the repo's established pattern for `stocks` and fund leaderboard):

### Option A1 (recommended): Postgres dual-write

- Add a tiny dual-write to `picks_shovels/store.py` and `narrative_radar/store.py`: when `postgres_enabled()` (see `backend/postgres_config.py`), write the snapshot rows to Postgres tables and read from Postgres first, falling back to SQLite locally.
- Schema: reuse the existing snapshot shape — one `*_snapshots` row (id, created_at, meta JSON) + N `*_rows` (payload JSON). Add migrations under `backend/migrations/postgres/` (e.g. `008_picks_shovels_snapshots.sql`, `009_narrative_radar_snapshots.sql`) following `006_stocks_sec_info.sql`.
- Because payloads are already JSON blobs, this is a thin change: store JSON in a `JSONB`/`TEXT` column; the scoring/engine code is untouched.
- **Pro:** matches repo conventions, transactional, queryable, durable. **Con:** needs the Postgres tables/migrations.

### Option A2: GCS JSON blob (simplest)

- On scan completion, write `latest.json` (the full overview/stocks payload) to `gs://$GCS_BUCKET/precomputed/picks_shovels/latest.json` and `.../narrative_radar/latest.json` (reuse the GCS client in `backend/ingestion_agent.py`).
- Read path: the router loads the blob (cached in-memory ~5 min) → instant, instance-independent.
- **Pro:** trivial, no schema, perfect for "serve a precomputed JSON." **Con:** not queryable/filterable server-side (filters move to the client or re-derive from the blob).

### Option A3: Supabase / data-lake

- Reuse Supabase (already the prod `VECTOR_BACKEND`/`DECISION_BACKEND`) or a BigQuery table. Heavier than needed for two small daily snapshots.

**Recommendation:** **A1 (Postgres dual-write)** for parity with the rest of the platform and to keep the existing server-side filtering (theme/score/phase/confidence) working. A2 is a fine fast-start if Postgres wiring is deferred.

---

## 4. Pillar B — daily warm via external cron (reliable at scale-zero)

### 4.1 Secured warm endpoints (reuse `require_cron_secret`)

| Page | Endpoint | Status |
|---|---|---|
| Narrative Radar | `POST /knowledge/narrative-radar-run` | **exists** (added with NR-9) — change to **run synchronously** (await `run_scan`) so the instance stays alive until the durable snapshot is written |
| Picks & Shovels | `POST /knowledge/picks-shovels-run` (new) — mirror the narrative-radar endpoint; `await picks_shovels.engine.run_scan(..., force=True)` | **add** |
| Fund Leaderboard | `POST /api/funds/ingest/run` (exists) for the heavy weekly 13F ingest; optionally a lighter `POST /api/funds/refresh-metrics` for a daily returns/metrics recompute | reuse + optional add |

All guarded by `PIPELINE_CRON_SECRET` (`backend/cron_auth.py`). Synchronous execution is the key change vs. today's fire-and-forget `start_scan_task` (which can be killed at scale-zero).

### 4.2 One GitHub Actions schedule (reuse the existing pattern)

Add `.github/workflows/precompute-pages.yml` modeled on `render-daily-pipeline.yml`:

```yaml
# ~01:10 UTC daily (after the 00:00 knowledge pipeline; before 02:10 grader)
on:
  schedule: [{ cron: "10 1 * * *" }]
  workflow_dispatch: {}
jobs:
  warm:
    runs-on: ubuntu-latest
    steps:
      - run: curl -fsS -X POST "$API/knowledge/picks-shovels-run"   -H "X-Cron-Secret: $SECRET" --max-time 600
      - run: curl -fsS -X POST "$API/knowledge/narrative-radar-run" -H "X-Cron-Secret: $SECRET" --max-time 600
      # Leaderboard: weekly (13F is quarterly) — separate cron "0 6 * * 1"
```

- `--max-time 600` keeps the HTTP request open while the scan runs (Cloud Run keeps the instance warm for the request; default request timeout is generous — raise the service timeout if needed).
- Secrets: `API` (Cloud Run URL), `SECRET` (`PIPELINE_CRON_SECRET`) in GitHub repo secrets.

### 4.3 Heavy jobs → Cloud Run Jobs (optional, for the leaderboard)

The 13F ingest is heavy and already has a Cloud Run Job (`fund-leaderboard-ingest`, see `backend/routers/pipeline_ops.py`). Keep it **weekly** via Cloud Scheduler → Cloud Run Job, independent of the API service timeout. P&S and Narrative Radar scans (~70 tickers, 1–2 min) are light enough to run inside the synchronous cron request, but can also be promoted to Cloud Run Jobs if they grow.

### 4.4 Keep in-process schedulers as secondary

Leave the existing APScheduler jobs (`narrative_radar_daily`, add `picks_shovels_daily`) and the `FUND_LB_SCHEDULE_ENABLE` weekly job in place for always-on / local deployments, but treat **external cron as authoritative** in production (documented in `docs/CRON.md`).

---

## 5. Pillar C — cache-first frontend (no forced manual run)

The pages already auto-fetch the latest snapshot on mount; the changes make them robust and instant:

1. **Always render the latest snapshot immediately** (already the case once a durable snapshot exists). Keep the manual "Refresh" button but demote it to secondary — the page is never blank when a snapshot exists.
2. **Friendly cold-start state.** If truly no snapshot exists (first deploy, before first cron), show a lightweight "Preparing today's data…" state instead of "click Run", and **optionally** trigger a one-time background warm (non-blocking) so the next visit is populated.
3. **Stale-while-revalidate.** Show the data instantly with an "updated X ago" badge (already present via `age_seconds`/`is_fresh`). If stale (older than TTL), fire a background refresh without blocking the view.
4. **Files:** `frontend/src/PicksShovelsUI.jsx`, `frontend/src/NarrativeRadarUI.jsx`, `frontend/src/intelligence/funds/FundLeaderboardUI.jsx`. All already use `apiFetch` + poll patterns; the change is the empty-state UX + optional auto-warm, not new plumbing.

---

## 6. Staleness / TTL policy

| Page | Refresh cadence | Serve-stale TTL | Rationale |
|---|---|---|---|
| Picks & Shovels | daily (01:10 UTC cron) | serve any snapshot instantly; background-refresh if > 24h | momentum shifts daily |
| Narrative Radar | daily (01:10 UTC cron) | same | theme lifecycle moves over days |
| Fund Leaderboard | weekly ingest + optional daily metrics | serve instantly; refresh weekly | 13F is quarterly |

The engines already implement a snapshot cache TTL (`PICKS_SHOVELS_CACHE_TTL_S`, `NARRATIVE_RADAR_CACHE_TTL_S`, default 3600s) so a same-day cron re-run or a user refresh reuses the fresh snapshot rather than rescanning.

---

## 7. Cold-start / first-deploy safety net

- **Warm-on-startup (guarded):** in `backend/main.py` startup (or `daily_pipeline`), if `NARRATIVE_RADAR_WARM_ON_START=1` / `PICKS_SHOVELS_WARM_ON_START=1` and no fresh durable snapshot exists, kick a one-time background scan. Off by default (don't slow cold starts); useful right after a deploy.
- **Idempotent cron:** the first scheduled cron after deploy populates the durable snapshot; every page load thereafter is instant.
- **Never block:** all warm paths are server-side/cron; the browser only ever does fast reads.

---

## 8. Implementation phases

| Phase | Work | Acceptance |
|---|---|---|
| **P1 — Durable storage** | Add Postgres dual-write (Option A1) to `picks_shovels/store.py` + `narrative_radar/store.py` (+ migrations); leaderboard already durable | A snapshot written on instance A is read by instance B; survives a simulated cold start (offline test seeds Postgres/temp DB and reads back) |
| **P2 — Synchronous warm endpoints** | Make `/knowledge/narrative-radar-run` await the scan; add `/knowledge/picks-shovels-run`; (optional) `/api/funds/refresh-metrics` | `curl -X POST` returns only after the durable snapshot is written; subsequent `GET` is instant + non-empty |
| **P3 — GitHub Actions cron** | Add `.github/workflows/precompute-pages.yml` (daily P&S + Narrative; weekly leaderboard); document secrets in `docs/CRON.md` | Scheduled run warms all three; verified via workflow logs + a non-empty `GET` afterward |
| **P4 — Frontend cache-first UX** | Cold-start "preparing" state + stale-while-revalidate + demote manual Run on all three pages | With a snapshot present, page renders < 1s with data and no "click Run"; cold start shows "preparing", not a dead end |
| **P5 — Cold-start warm + flags** | Optional warm-on-startup flags; keep in-process schedulers as secondary; add `picks_shovels_daily` APScheduler job | Fresh deploy self-heals within one cron cycle; flags documented |

---

## 9. Files touched (map)

- **Durable store:** `backend/picks_shovels/store.py`, `backend/narrative_radar/store.py`, `backend/migrations/postgres/008_*`, `009_*`, reuse `backend/postgres_config.py`.
- **Warm endpoints:** `backend/routers/knowledge.py` (picks-shovels-run; make narrative-radar-run synchronous), `backend/routers/fund_leaderboard.py` (optional refresh-metrics), reuse `backend/cron_auth.py`.
- **Engines (reused, unchanged):** `backend/picks_shovels/engine.py`, `backend/narrative_radar/engine.py`, `backend/fund_leaderboard_job.py`.
- **Schedulers (secondary):** `backend/daily_pipeline.py` (add `picks_shovels_daily`), `backend/main.py` (leaderboard weekly already present).
- **Cron:** `.github/workflows/precompute-pages.yml`, docs in `docs/CRON.md`.
- **Frontend:** `frontend/src/PicksShovelsUI.jsx`, `frontend/src/NarrativeRadarUI.jsx`, `frontend/src/intelligence/funds/FundLeaderboardUI.jsx`.

---

## 10. Risks & mitigations

- **Cloud Run request timeout vs. scan duration.** Run warm synchronously within the cron request and raise the service timeout if a scan exceeds it; promote to a Cloud Run Job if scans grow. The light P&S/Narrative scans (~1–2 min) fit comfortably.
- **Cost of daily scans.** One shared scan per page per day (not per user); the snapshot TTL prevents duplicate scans within a day. yfinance rate limits already handled by the engines' chunked-fetch + delays.
- **Postgres not configured locally.** Dual-write degrades to SQLite (the existing `postgres_enabled()` guard), so local dev is unaffected.
- **Stale data on a missed cron.** Stale-while-revalidate still serves the last good snapshot instantly and self-heals on the next cron or manual refresh.
- **Signal families flag-gated (Narrative Radar).** Unrelated to this plan; the cron warm simply runs whatever families are enabled (defaults stay MVP-fast).

---

## 11. Success criteria

1. Visiting `/picks-shovels`, `/narrative-radar`, and `/intelligence/funds/leaderboard` renders **populated data in < 1s** with no manual action, on a fresh Cloud Run instance.
2. Data refreshes automatically once per day (weekly for the leaderboard) via external cron, independent of scale-to-zero.
3. Snapshots survive cold starts (durable store) and are identical for all users.
4. The manual "Refresh" remains available but is never required to see data.
5. No page load blocks on a live scan.
