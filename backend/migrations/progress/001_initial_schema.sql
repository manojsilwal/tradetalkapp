-- Initial schema for progress.db (users + user_progress + xp_history).
-- This matches the CREATE TABLE IF NOT EXISTS statements already in auth.py and user_progress.py.
-- Future migrations add columns or indexes incrementally.

CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    name       TEXT DEFAULT '',
    avatar     TEXT DEFAULT '',
    created_at REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_progress (
    user_id        TEXT PRIMARY KEY,
    xp             INTEGER DEFAULT 0,
    level          INTEGER DEFAULT 1,
    level_title    TEXT    DEFAULT 'Novice',
    streak_days    INTEGER DEFAULT 0,
    last_active    TEXT    DEFAULT '',
    total_actions  TEXT    DEFAULT '{}',
    badges         TEXT    DEFAULT '[]',
    created_at     REAL    DEFAULT 0
);

CREATE TABLE IF NOT EXISTS xp_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    xp_awarded  INTEGER NOT NULL,
    note        TEXT    DEFAULT '',
    timestamp   REAL    NOT NULL
);
