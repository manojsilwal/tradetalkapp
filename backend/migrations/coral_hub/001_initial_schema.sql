-- CORAL-style structured hub: notes, skills, attempts (SQLite, progress.db)

CREATE TABLE IF NOT EXISTS coral_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    observation TEXT NOT NULL,
    market_regime TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL
);

CREATE INDEX IF NOT EXISTS idx_coral_notes_created ON coral_notes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_coral_notes_regime ON coral_notes(market_regime);

CREATE TABLE IF NOT EXISTS coral_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    contributed_by TEXT NOT NULL DEFAULT '',
    times_used INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL
);

CREATE INDEX IF NOT EXISTS idx_coral_skills_used ON coral_skills(times_used DESC);

CREATE TABLE IF NOT EXISTS coral_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    signal REAL,
    score REAL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coral_attempts_task ON coral_attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_coral_attempts_created ON coral_attempts(created_at DESC);
