-- Phase C — minimal entity + claim rows (auditable factual statements with source refs).
-- Stored in progress.db alongside preferences / CORAL.

CREATE TABLE IF NOT EXISTS claim_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'ticker',
    symbol TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    claim_text TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES claim_entities(id)
);

CREATE INDEX IF NOT EXISTS idx_claim_rows_entity ON claim_rows(entity_id);
CREATE INDEX IF NOT EXISTS idx_claim_rows_created ON claim_rows(created_at DESC);
