-- Stocks metadata & SEC/insider transaction cache table
CREATE TABLE IF NOT EXISTS stocks (
    ticker               TEXT PRIMARY KEY,
    ceo_name             TEXT,
    sitg_score           DOUBLE PRECISION,
    ceo_base_salary      DOUBLE PRECISION,
    sitg_value           DOUBLE PRECISION,
    sitg_multiple        DOUBLE PRECISION,
    sitg_percentile_tier TEXT,
    insider_buy_count_12m INTEGER,
    insider_sell_count_12m INTEGER,
    insider_net_shares_12m DOUBLE PRECISION,
    held_percent_insiders DOUBLE PRECISION,
    updated_at           DOUBLE PRECISION
);
