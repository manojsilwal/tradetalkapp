-- Macro flow / thematic rotation (SQLite). Separate DB file (macro_flow.db).

CREATE TABLE IF NOT EXISTS macro_categories (
    category_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    parent_id     TEXT,
    level         INTEGER DEFAULT 1,
    chain_position INTEGER,
    color_hex     TEXT DEFAULT '#6366f1',
    description   TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS macro_entities (
    entity_id     TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    asset_type    TEXT DEFAULT 'stock',
    market_cap    INTEGER,
    is_active     INTEGER DEFAULT 1,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_category_map (
    entity_id     TEXT NOT NULL,
    category_id   TEXT NOT NULL,
    weight        REAL NOT NULL,
    is_primary    INTEGER DEFAULT 0,
    added_by      TEXT DEFAULT 'manual',
    PRIMARY KEY (entity_id, category_id)
);

CREATE TABLE IF NOT EXISTS flow_scores (
    category_id   TEXT NOT NULL,
    timestamp     REAL NOT NULL,
    interval      TEXT NOT NULL,
    cmf           REAL,
    rs_ratio      REAL,
    rs_momentum   REAL,
    flow_score    REAL,
    confidence    REAL,
    PRIMARY KEY (category_id, timestamp, interval)
);

CREATE INDEX IF NOT EXISTS idx_flow_scores_ts ON flow_scores(timestamp DESC);

CREATE TABLE IF NOT EXISTS qual_scores (
    entity_id         TEXT NOT NULL,
    scored_at         REAL NOT NULL,
    moat_rating       INTEGER DEFAULT 0,
    management_score  REAL,
    earnings_quality  REAL,
    margin_trend      REAL,
    balance_sheet     REAL,
    overall_qual      REAL,
    fundamental_band  TEXT,
    source            TEXT DEFAULT 'yfinance_info',
    PRIMARY KEY (entity_id, scored_at)
);

CREATE TABLE IF NOT EXISTS category_qual_scores (
    category_id         TEXT NOT NULL,
    scored_at           REAL NOT NULL,
    weighted_qual_score REAL,
    fundamental_band    TEXT,
    moat_wide_pct       REAL,
    coverage_pct        REAL,
    PRIMARY KEY (category_id, scored_at)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id           TEXT PRIMARY KEY,
    source_category   TEXT NOT NULL,
    target_category   TEXT NOT NULL,
    relationship_type TEXT,
    lag_days          INTEGER DEFAULT 0,
    base_strength     REAL,
    description       TEXT
);

CREATE TABLE IF NOT EXISTS edge_flows (
    edge_id         TEXT NOT NULL,
    timestamp       REAL NOT NULL,
    interval        TEXT NOT NULL,
    flow_magnitude  REAL,
    direction       INTEGER,
    confidence      REAL,
    PRIMARY KEY (edge_id, timestamp, interval)
);

CREATE TABLE IF NOT EXISTS flow_qa_decisions (
    decision_id       TEXT PRIMARY KEY,
    category_id       TEXT,
    timestamp         REAL NOT NULL,
    interval          TEXT,
    quant_flow_score  REAL,
    qual_node_score   REAL,
    qa_verdict        TEXT,
    confidence        REAL,
    conflict_flag     INTEGER DEFAULT 0,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_flow_qa_ts ON flow_qa_decisions(timestamp DESC);
