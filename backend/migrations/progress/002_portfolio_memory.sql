-- Your Morning v0 — portfolio memory tables + extended snapshot columns.
-- Applied to progress.db via backend/migrations/runner.py.

-- Base snapshot table (also created in paper_portfolio.init_portfolio_db).
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    user_id         TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,
    portfolio_value REAL NOT NULL,
    spy_value       REAL NOT NULL,
    positions_json  TEXT NOT NULL,
    recorded_at     REAL NOT NULL,
    PRIMARY KEY (user_id, snapshot_date)
);

-- Extend portfolio_snapshots with Your Morning fields.
ALTER TABLE portfolio_snapshots ADD COLUMN daily_return_pct REAL;
ALTER TABLE portfolio_snapshots ADD COLUMN daily_return_value REAL;
ALTER TABLE portfolio_snapshots ADD COLUMN cumulative_return_pct REAL;
ALTER TABLE portfolio_snapshots ADD COLUMN qqq_return_pct REAL;
ALTER TABLE portfolio_snapshots ADD COLUMN top_position_symbol TEXT;
ALTER TABLE portfolio_snapshots ADD COLUMN top_position_weight REAL;
ALTER TABLE portfolio_snapshots ADD COLUMN sector_exposures TEXT DEFAULT '{}';

CREATE TABLE IF NOT EXISTS portfolio_events (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    symbol       TEXT,
    event_date   TEXT NOT NULL,
    title        TEXT,
    description  TEXT,
    metadata     TEXT DEFAULT '{}',
    created_at   REAL NOT NULL
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
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_actions_user_created
    ON user_actions (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio_reaction_memory (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    event_date              TEXT NOT NULL,
    move_pct                REAL,
    portfolio_impact_value  REAL,
    portfolio_impact_pct    REAL,
    cause_category          TEXT,
    one_line_reason         TEXT,
    source_event_id         TEXT,
    metadata                TEXT DEFAULT '{}',
    created_at              REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reaction_user_sym_date
    ON portfolio_reaction_memory (user_id, symbol, event_date);
