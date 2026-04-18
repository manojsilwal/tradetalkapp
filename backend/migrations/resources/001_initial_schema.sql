-- RSPL Phase A: Resource registry schema (see docs/RESOURCE_REGISTRY.md)
--
-- Modeled after Autogenesis Protocol (arXiv:2604.15034). Stores versioned,
-- protocol-registered resource records with full lineage for audit and rollback.
-- Phase A scope: PROMPT resources only. Schema is intentionally generic so that
-- later phases can register AGENT / TOOL / ENV / MEM resources without migration.

CREATE TABLE IF NOT EXISTS resource_records (
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    version       TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    learnable     INTEGER NOT NULL DEFAULT 0,   -- 0/1; g_{tau,i} in paper
    body          TEXT NOT NULL,
    schema_json   TEXT,                          -- F_{tau,i} (LLM-facing output schema)
    fallback_json TEXT,                          -- fallback template when resource fails
    metadata_json TEXT NOT NULL DEFAULT '{}',    -- m_{tau,i}
    source_path   TEXT NOT NULL DEFAULT '',      -- eta_{tau,i} (yaml path or 'inline')
    created_at    REAL NOT NULL,
    PRIMARY KEY (name, version)
);

CREATE INDEX IF NOT EXISTS idx_resource_records_kind ON resource_records(kind);
CREATE INDEX IF NOT EXISTS idx_resource_records_name ON resource_records(name);

-- Active version pointer per resource name (one row per name)
CREATE TABLE IF NOT EXISTS resource_active (
    name       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    version    TEXT NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (name, version) REFERENCES resource_records(name, version)
);

-- Audit lineage: one row per register/update/restore operation
CREATE TABLE IF NOT EXISTS resource_lineage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    from_version   TEXT,                         -- NULL on initial register
    to_version     TEXT NOT NULL,
    operation      TEXT NOT NULL,                -- register | update | restore | reseed
    reason         TEXT NOT NULL DEFAULT '',
    actor          TEXT NOT NULL DEFAULT 'system',  -- seed:yaml | human:<id> | sepl:<id>
    created_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resource_lineage_name    ON resource_lineage(name);
CREATE INDEX IF NOT EXISTS idx_resource_lineage_created ON resource_lineage(created_at DESC);
