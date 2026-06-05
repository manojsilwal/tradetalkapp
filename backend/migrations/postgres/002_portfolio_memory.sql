-- Your Morning v0 — portfolio memory (Cloud SQL / Postgres).

ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS daily_return_pct DOUBLE PRECISION;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS daily_return_value DOUBLE PRECISION;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS cumulative_return_pct DOUBLE PRECISION;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS qqq_return_pct DOUBLE PRECISION;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS top_position_symbol TEXT;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS top_position_weight DOUBLE PRECISION;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS sector_exposures TEXT DEFAULT '{}';

CREATE TABLE IF NOT EXISTS portfolio_events (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    symbol       TEXT,
    event_date   TEXT NOT NULL,
    title        TEXT,
    description  TEXT,
    metadata     TEXT DEFAULT '{}',
    created_at   DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolio_events_user_date
    ON portfolio_events (user_id, event_date DESC);

CREATE TABLE IF NOT EXISTS user_actions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    TEXT,
    symbol       TEXT,
    page         TEXT,
    metadata     TEXT DEFAULT '{}',
    created_at   DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_actions_user_created
    ON user_actions (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio_reaction_memory (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    event_date              TEXT NOT NULL,
    move_pct                DOUBLE PRECISION,
    portfolio_impact_value  DOUBLE PRECISION,
    portfolio_impact_pct    DOUBLE PRECISION,
    cause_category          TEXT,
    one_line_reason         TEXT,
    source_event_id         TEXT,
    metadata                TEXT DEFAULT '{}',
    created_at              DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reaction_user_sym_date
    ON portfolio_reaction_memory (user_id, symbol, event_date);
