-- Initial schema for alerts.db (notification alerts).
-- Matches the CREATE TABLE IF NOT EXISTS in alert_store.py.

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT,
    urgency INTEGER DEFAULT 5,
    urgency_label TEXT DEFAULT 'moderate',
    affected_sectors TEXT DEFAULT '[]',
    source TEXT DEFAULT 'Unknown',
    source_reliability TEXT DEFAULT 'low',
    source_reliability_score REAL DEFAULT 0.4,
    link TEXT DEFAULT '',
    timestamp REAL NOT NULL,
    is_read INTEGER DEFAULT 0
);
