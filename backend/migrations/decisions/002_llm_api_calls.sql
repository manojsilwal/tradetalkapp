-- Migration 002: LLM API Calls tracking table
CREATE TABLE IF NOT EXISTS llm_api_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    REAL NOT NULL,                -- epoch seconds (UTC)
    query_brief  TEXT NOT NULL,                -- summary or brief snippet of the query/prompt
    llm_used     TEXT NOT NULL,                -- model identifier
    cost         REAL NOT NULL,                -- estimated cost in USD
    time_taken   REAL NOT NULL                 -- latency in seconds
);

CREATE INDEX IF NOT EXISTS idx_llm_api_calls_timestamp ON llm_api_calls(timestamp DESC);
