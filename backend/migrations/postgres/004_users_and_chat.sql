-- Users, preferences, chat sessions & message history (Cloud SQL / Postgres).
-- Applied by backend/auth_pg.py on startup when PORTFOLIO_STORAGE=postgres.

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL,
    name          TEXT DEFAULT '',
    avatar        TEXT DEFAULT '',
    password_hash TEXT,
    created_at    DOUBLE PRECISION DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (LOWER(email));

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id     TEXT PRIMARY KEY,
    preferences TEXT DEFAULT '{}',
    signals     TEXT DEFAULT '{}',
    updated_at  DOUBLE PRECISION DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id   TEXT PRIMARY KEY,
    user_id      TEXT,
    assembled_at DOUBLE PRECISION NOT NULL,
    expires_at   DOUBLE PRECISION NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_expires ON chat_sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions (user_id);

CREATE TABLE IF NOT EXISTS chat_message_history (
    id         SERIAL PRIMARY KEY,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_hist_user_session
    ON chat_message_history (user_id, session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_hist_user_activity
    ON chat_message_history (user_id, created_at DESC);
