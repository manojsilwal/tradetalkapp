-- Supabase / Postgres bootstrap for the Decision-Outcome Ledger.
--
-- This is the Postgres equivalent of
-- backend/migrations/decisions/001_initial.sql. Apply once against your
-- Supabase project when DECISION_BACKEND=supabase. Safe to re-run (every
-- object uses IF NOT EXISTS). RLS is OFF by default -- only service-role
-- (backend) writes here; adjust policies in the Supabase dashboard if you
-- plan to expose read access to end users.

-- ── decision_events ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS decision_events (
    decision_id              TEXT PRIMARY KEY,
    created_at               DOUBLE PRECISION NOT NULL,
    user_id                  TEXT NOT NULL DEFAULT '',
    decision_type            TEXT NOT NULL,
    symbol                   TEXT NOT NULL DEFAULT '',
    horizon_hint             TEXT NOT NULL DEFAULT 'none',
    model                    TEXT NOT NULL DEFAULT '',
    prompt_versions_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    registry_snapshot_id     TEXT NOT NULL DEFAULT '',
    inputs_hash              TEXT NOT NULL DEFAULT '',
    output_json              JSONB NOT NULL DEFAULT '{}'::jsonb,
    verdict                  TEXT NOT NULL DEFAULT '',
    confidence               DOUBLE PRECISION,
    source_route             TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_decision_events_created_at ON decision_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_events_type       ON decision_events(decision_type);
CREATE INDEX IF NOT EXISTS idx_decision_events_symbol     ON decision_events(symbol);
CREATE INDEX IF NOT EXISTS idx_decision_events_user       ON decision_events(user_id);
CREATE INDEX IF NOT EXISTS idx_decision_events_model      ON decision_events(model);
CREATE INDEX IF NOT EXISTS idx_decision_events_type_sym   ON decision_events(decision_type, symbol);


-- ── decision_evidence ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS decision_evidence (
    id           BIGSERIAL PRIMARY KEY,
    decision_id  TEXT NOT NULL REFERENCES decision_events(decision_id) ON DELETE CASCADE,
    chunk_id     TEXT NOT NULL,
    collection   TEXT NOT NULL DEFAULT '',
    relevance    DOUBLE PRECISION,
    rank         INTEGER NOT NULL DEFAULT 0,
    created_at   DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decision_evidence_decision ON decision_evidence(decision_id);
CREATE INDEX IF NOT EXISTS idx_decision_evidence_chunk    ON decision_evidence(chunk_id);
CREATE INDEX IF NOT EXISTS idx_decision_evidence_coll     ON decision_evidence(collection);


-- ── outcome_observations ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS outcome_observations (
    id            BIGSERIAL PRIMARY KEY,
    decision_id   TEXT NOT NULL REFERENCES decision_events(decision_id) ON DELETE CASCADE,
    horizon       TEXT NOT NULL,
    as_of_ts      DOUBLE PRECISION NOT NULL,
    metric        TEXT NOT NULL,
    value         DOUBLE PRECISION,
    benchmark     TEXT NOT NULL DEFAULT '',
    excess_return DOUBLE PRECISION,
    correct_bool  SMALLINT,
    label_source  TEXT NOT NULL DEFAULT '',
    created_at    DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outcome_obs_decision ON outcome_observations(decision_id);
CREATE INDEX IF NOT EXISTS idx_outcome_obs_horizon  ON outcome_observations(horizon);
CREATE INDEX IF NOT EXISTS idx_outcome_obs_created  ON outcome_observations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_obs_metric   ON outcome_observations(metric);
CREATE UNIQUE INDEX IF NOT EXISTS uq_outcome_obs_unique
    ON outcome_observations(decision_id, horizon, metric);


-- ── feature_snapshots ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feature_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    decision_id   TEXT NOT NULL REFERENCES decision_events(decision_id) ON DELETE CASCADE,
    feature_name  TEXT NOT NULL,
    value_num     DOUBLE PRECISION,
    value_str     TEXT NOT NULL DEFAULT '',
    regime        TEXT NOT NULL DEFAULT '',
    created_at    DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_snap_decision ON feature_snapshots(decision_id);
CREATE INDEX IF NOT EXISTS idx_feature_snap_name     ON feature_snapshots(feature_name);
CREATE INDEX IF NOT EXISTS idx_feature_snap_regime   ON feature_snapshots(regime);


-- ── contract_violations ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contract_violations (
    id                BIGSERIAL PRIMARY KEY,
    decision_id       TEXT NOT NULL DEFAULT '',
    resource_name     TEXT NOT NULL,
    resource_version  TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    path              TEXT NOT NULL DEFAULT '$',
    code              TEXT NOT NULL,
    message           TEXT NOT NULL DEFAULT '',
    observed_type     TEXT NOT NULL DEFAULT '',
    expected          TEXT NOT NULL DEFAULT '',
    created_at        DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contract_viol_created  ON contract_violations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_contract_viol_resource ON contract_violations(resource_name);
CREATE INDEX IF NOT EXISTS idx_contract_viol_model    ON contract_violations(model);
CREATE INDEX IF NOT EXISTS idx_contract_viol_code     ON contract_violations(code);
CREATE INDEX IF NOT EXISTS idx_contract_viol_decision ON contract_violations(decision_id);
