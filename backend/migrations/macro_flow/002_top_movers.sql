-- Optional JSON array of {symbol, period_change_pct} for Agent intel / UI.
ALTER TABLE flow_scores ADD COLUMN top_movers_json TEXT;
