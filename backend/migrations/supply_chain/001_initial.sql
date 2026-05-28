-- Supply chain capital flow (SQLite). Separate DB file (supply_chain.db).

CREATE TABLE IF NOT EXISTS supply_chain_nodes (
    node_id           TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    ticker            TEXT,
    gics_sector       TEXT NOT NULL,
    gics_sub_industry TEXT,
    is_public         INTEGER DEFAULT 1,
    metadata_json     TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS supply_chain_edges (
    edge_id                TEXT PRIMARY KEY,
    source_node_id         TEXT NOT NULL,
    target_node_id         TEXT NOT NULL,
    relationship_type      TEXT,
    amount_est_usd         REAL,
    amount_pct_of_revenue  REAL,
    timestamp_year         INTEGER NOT NULL,
    confidence             REAL DEFAULT 0.5,
    source                 TEXT NOT NULL,
    citation               TEXT,
    FOREIGN KEY (source_node_id) REFERENCES supply_chain_nodes(node_id),
    FOREIGN KEY (target_node_id) REFERENCES supply_chain_nodes(node_id)
);

CREATE INDEX IF NOT EXISTS idx_sce_year   ON supply_chain_edges(timestamp_year);
CREATE INDEX IF NOT EXISTS idx_sce_source ON supply_chain_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_sce_target ON supply_chain_edges(target_node_id);
