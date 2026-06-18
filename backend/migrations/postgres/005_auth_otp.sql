-- OTP sessions for email 2FA sign-in (Cloud SQL / Postgres).

CREATE TABLE IF NOT EXISTS auth_otp_sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    otp_hash    TEXT NOT NULL,
    expires_at  DOUBLE PRECISION NOT NULL,
    attempts    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_auth_otp_user ON auth_otp_sessions (user_id);
