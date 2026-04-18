-- Decision-Outcome Ledger — Phase 2 of Harness Engineering moat (SQLite).
--
-- Stores ONE row per user-facing agent decision (swarm verdict, debate call,
-- chat tool answer, decision terminal payload, scorecard ratio, gold advisor
-- view, etc.) with full provenance so every decision can later be joined to
-- market truth and correlated to the input datapoints / prompt+model versions
-- that produced it.
--
-- Design axioms:
--   * Append-only. No UPDATE or DELETE on decision_events -- graders write
--     outcomes to a child table instead. This preserves an auditable history
--     when prompts or models change.
--   * Model-swap safe. Every row stamps prompt_versions_json +
--     registry_snapshot_id + model so the exact inference state that produced
--     the decision can be reconstructed later.
--   * Supabase-friendly shape. Same columns + indexes map to Postgres by
--     swapping REAL -> DOUBLE PRECISION, INTEGER PK AUTOINCREMENT ->
--     BIGSERIAL, and TEXT JSON columns -> JSONB.
--
-- See docs/DECISION_LEDGER.md for usage notes and example queries.


-- ── decision_events ──────────────────────────────────────────────────────
-- One row per agent decision. decision_id is the external identifier callers
-- can use to attach evidence / features / outcomes later.

CREATE TABLE IF NOT EXISTS decision_events (
    decision_id              TEXT PRIMARY KEY,                 -- uuid hex; caller-supplied
    created_at               REAL NOT NULL,                    -- epoch seconds (UTC)
    user_id                  TEXT NOT NULL DEFAULT '',         -- '' for system-generated
    decision_type            TEXT NOT NULL,                    -- swarm | debate | chat_tool | decision_terminal | scorecard | gold_advisor | ...
    symbol                   TEXT NOT NULL DEFAULT '',         -- ticker; '' when not applicable (e.g. macro summary)
    horizon_hint             TEXT NOT NULL DEFAULT 'none',     -- '1d' | '5d' | '21d' | '63d' | 'none'
    model                    TEXT NOT NULL DEFAULT '',         -- OPENROUTER_MODEL / GEMINI model id at call time
    prompt_versions_json     TEXT NOT NULL DEFAULT '{}',       -- {"role": "1.2.3", ...}
    registry_snapshot_id     TEXT NOT NULL DEFAULT '',         -- resource_registry.snapshot_id() when emitted
    inputs_hash              TEXT NOT NULL DEFAULT '',         -- sha256 of (prompt + inputs); dedupe key
    output_json              TEXT NOT NULL DEFAULT '{}',       -- the structured agent output
    verdict                  TEXT NOT NULL DEFAULT '',         -- extracted enum for fast grading
    confidence               REAL,                             -- 0..1; may be NULL
    source_route             TEXT NOT NULL DEFAULT ''          -- e.g. 'backend/agents.py::AgentPair.run'
);

CREATE INDEX IF NOT EXISTS idx_decision_events_created_at ON decision_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_events_type       ON decision_events(decision_type);
CREATE INDEX IF NOT EXISTS idx_decision_events_symbol     ON decision_events(symbol);
CREATE INDEX IF NOT EXISTS idx_decision_events_user       ON decision_events(user_id);
CREATE INDEX IF NOT EXISTS idx_decision_events_model      ON decision_events(model);
CREATE INDEX IF NOT EXISTS idx_decision_events_type_sym   ON decision_events(decision_type, symbol);


-- ── decision_evidence ────────────────────────────────────────────────────
-- Which RAG chunks or data-lake slices informed the decision. Populated at
-- emit time from the retrieval results (Chroma/Supabase ids + similarity).
-- This is the "provenance" layer that lets correlation queries ask
-- "which chunks cause wrong answers" and lets replay reproduce context.

CREATE TABLE IF NOT EXISTS decision_evidence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id  TEXT NOT NULL,
    chunk_id     TEXT NOT NULL,                -- vector-store id or 'collection:<rowid>'
    collection   TEXT NOT NULL DEFAULT '',     -- e.g. swarm_history, stock_profiles
    relevance    REAL,                         -- similarity score; NULL if unknown
    rank         INTEGER NOT NULL DEFAULT 0,   -- 0-based position in the retrieval
    created_at   REAL NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decision_events(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_evidence_decision ON decision_evidence(decision_id);
CREATE INDEX IF NOT EXISTS idx_decision_evidence_chunk    ON decision_evidence(chunk_id);
CREATE INDEX IF NOT EXISTS idx_decision_evidence_coll     ON decision_evidence(collection);


-- ── outcome_observations ────────────────────────────────────────────────
-- Graded market-truth observations. Written by backend/outcome_grader.py at
-- multiple horizons per decision (1d / 5d / 21d / 63d + benchmark-relative +
-- risk-adjusted). Paper-portfolio PnL lands here as horizon='paper'.

CREATE TABLE IF NOT EXISTS outcome_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   TEXT NOT NULL,
    horizon       TEXT NOT NULL,                -- '1d' | '5d' | '21d' | '63d' | 'paper'
    as_of_ts      REAL NOT NULL,                -- epoch seconds of the as-of close
    metric        TEXT NOT NULL,                -- price_return_pct | excess_return_vs_spy_pct | risk_adjusted_return | paper_pnl
    value         REAL,                         -- metric value (NULL when not computable)
    benchmark     TEXT NOT NULL DEFAULT '',     -- e.g. 'SPY'
    excess_return REAL,                         -- metric - benchmark return (nullable)
    correct_bool  INTEGER,                      -- 0/1 correctness against verdict (NULL for non-binary metrics)
    label_source  TEXT NOT NULL DEFAULT '',     -- yfinance | data_lake | paper_portfolio
    created_at    REAL NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decision_events(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_outcome_obs_decision ON outcome_observations(decision_id);
CREATE INDEX IF NOT EXISTS idx_outcome_obs_horizon  ON outcome_observations(horizon);
CREATE INDEX IF NOT EXISTS idx_outcome_obs_created  ON outcome_observations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_obs_metric   ON outcome_observations(metric);
-- Prevent duplicate grades for the same decision/horizon/metric.
CREATE UNIQUE INDEX IF NOT EXISTS uq_outcome_obs_unique
    ON outcome_observations(decision_id, horizon, metric);


-- ── feature_snapshots ───────────────────────────────────────────────────
-- The input datapoints the decision saw. Denormalized for fast correlation
-- queries (feature x regime x horizon hit-rate). One row per feature so the
-- set is extensible without schema migration.

CREATE TABLE IF NOT EXISTS feature_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   TEXT NOT NULL,
    feature_name  TEXT NOT NULL,                -- e.g. pe_ratio, sir, vix, market_regime
    value_num     REAL,                         -- numeric value when applicable
    value_str     TEXT NOT NULL DEFAULT '',     -- string value when applicable (e.g. regime labels)
    regime        TEXT NOT NULL DEFAULT '',     -- market regime at decision time (denormalized)
    created_at    REAL NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decision_events(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_feature_snap_decision ON feature_snapshots(decision_id);
CREATE INDEX IF NOT EXISTS idx_feature_snap_name     ON feature_snapshots(feature_name);
CREATE INDEX IF NOT EXISTS idx_feature_snap_regime   ON feature_snapshots(regime);
-- Queryable uniqueness: one row per (decision, feature_name) -- overwrites on
-- re-record are handled by the ledger module, not the DB.


-- ── contract_violations ─────────────────────────────────────────────────
-- Runtime schema violations emitted by backend/contract_validator.py. Lets
-- us answer "which model + prompt version drifts most" with a SQL GROUP BY.

CREATE TABLE IF NOT EXISTS contract_violations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id       TEXT NOT NULL DEFAULT '',       -- '' when not tied to a ledger row
    resource_name     TEXT NOT NULL,
    resource_version  TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    path              TEXT NOT NULL DEFAULT '$',      -- dot/array path inside the payload
    code              TEXT NOT NULL,                  -- stable code (missing_required, type_mismatch, ...)
    message           TEXT NOT NULL DEFAULT '',
    observed_type     TEXT NOT NULL DEFAULT '',
    expected          TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contract_viol_created   ON contract_violations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_contract_viol_resource  ON contract_violations(resource_name);
CREATE INDEX IF NOT EXISTS idx_contract_viol_model     ON contract_violations(model);
CREATE INDEX IF NOT EXISTS idx_contract_viol_code      ON contract_violations(code);
CREATE INDEX IF NOT EXISTS idx_contract_viol_decision  ON contract_violations(decision_id);
