-- Per-page user feedback (ratings + comments) for product improvement analytics.

CREATE TABLE IF NOT EXISTS page_feedback (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    page        TEXT NOT NULL,
    rating      INTEGER,
    comment     TEXT,
    symbol      TEXT,
    metadata    TEXT DEFAULT '{}',
    created_at  DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_page_feedback_page_created
    ON page_feedback (page, created_at DESC);
