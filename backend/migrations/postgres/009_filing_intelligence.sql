-- Filing intelligence cache for brain + agent surfaces (S&P 500 scope).
CREATE TABLE IF NOT EXISTS filing_intelligence (
    ticker                          TEXT PRIMARY KEY,
    as_of_date                      DATE NOT NULL,
    filing_form                     TEXT,
    filing_risk_score               REAL,
    management_tone_score           REAL,
    new_product_expansion_score     REAL,
    customer_concentration_score    REAL,
    demand_visibility_score         REAL,
    order_backlog_usd               BIGINT,
    backlog_growth_yoy_pct          REAL,
    book_to_bill_ratio              REAL,
    recurring_revenue_pct           REAL,
    top_customer_concentration_pct  REAL,
    end_market_exposure_json        JSONB,
    primary_moat_driver             TEXT,
    thematic_tags_json              JSONB,
    demand_visibility_summary       TEXT,
    citations_json                  JSONB,
    raw_extract_json                JSONB,
    extracted_at_utc                TIMESTAMPTZ NOT NULL,
    source                          TEXT DEFAULT 'fincrawler'
);

CREATE INDEX IF NOT EXISTS idx_filing_intel_extracted ON filing_intelligence(extracted_at_utc);
