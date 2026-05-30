-- Paper portfolio (Cloud SQL / Postgres). Applied by backend/paper_portfolio_pg.py on startup.

CREATE TABLE IF NOT EXISTS paper_positions (
    id            TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price   DOUBLE PRECISION NOT NULL,
    entry_date    TEXT NOT NULL,
    shares        DOUBLE PRECISION NOT NULL,
    allocated     DOUBLE PRECISION NOT NULL,
    source        TEXT DEFAULT 'manual',
    note          TEXT DEFAULT '',
    closed        INTEGER DEFAULT 0,
    exit_price    DOUBLE PRECISION,
    exit_date     TEXT,
    realised_pnl  DOUBLE PRECISION,
    sector        TEXT DEFAULT 'Unknown',
    market_cap    DOUBLE PRECISION,
    cap_bucket    TEXT DEFAULT 'Unknown',
    asset_type    TEXT DEFAULT 'Unknown',
    PRIMARY KEY (id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_user_open
    ON paper_positions (user_id, closed);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    user_id         TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,
    portfolio_value DOUBLE PRECISION NOT NULL,
    spy_value       DOUBLE PRECISION NOT NULL,
    positions_json  TEXT NOT NULL,
    recorded_at     DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (user_id, snapshot_date)
);
