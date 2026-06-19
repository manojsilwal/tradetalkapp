-- Handoff / trace events for nightly dreaming (debate + swarm "D/E" style signals).
CREATE TABLE IF NOT EXISTS coral_handoff_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coral_handoff_created ON coral_handoff_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_coral_handoff_type ON coral_handoff_events(event_type);
