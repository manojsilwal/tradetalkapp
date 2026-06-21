CREATE TABLE IF NOT EXISTS fund_master (
  fund_id UUID PRIMARY KEY,
  display_name TEXT NOT NULL,
  legal_name TEXT,
  cik TEXT,
  crd TEXT,
  sec_file_number TEXT,
  manager_type TEXT,
  strategy_tags JSONB DEFAULT '[]',
  include_in_leaderboard BOOLEAN DEFAULT TRUE,
  is_index_manager BOOLEAN DEFAULT FALSE,
  website TEXT,
  source_confidence NUMERIC,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sec_filings (
  filing_id UUID PRIMARY KEY,
  fund_id UUID REFERENCES fund_master(fund_id),
  cik TEXT NOT NULL,
  accession_number TEXT NOT NULL,
  form_type TEXT NOT NULL,
  report_period DATE,
  filing_date DATE,
  acceptance_datetime TIMESTAMPTZ,
  filing_url TEXT,
  primary_document TEXT,
  information_table_document TEXT,
  is_amendment BOOLEAN DEFAULT FALSE,
  amendment_number INTEGER,
  active_version BOOLEAN DEFAULT TRUE,
  raw_object_path TEXT,
  parsed_status TEXT DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(cik, accession_number)
);

CREATE TABLE IF NOT EXISTS thirteen_f_holdings_normalized (
  holding_id UUID PRIMARY KEY,
  filing_id UUID REFERENCES sec_filings(filing_id),
  fund_id UUID REFERENCES fund_master(fund_id),
  report_period DATE NOT NULL,
  security_id UUID,
  issuer_name TEXT,
  title_of_class TEXT,
  cusip TEXT,
  figi TEXT,
  shares_or_principal_amount NUMERIC,
  shares_or_principal_type TEXT,
  market_value_usd NUMERIC,
  put_call TEXT,
  investment_discretion TEXT,
  other_manager TEXT,
  voting_authority_sole NUMERIC,
  voting_authority_shared NUMERIC,
  voting_authority_none NUMERIC,
  holding_weight NUMERIC,
  mapping_status TEXT DEFAULT 'unmapped',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS security_master (
  security_id UUID PRIMARY KEY,
  figi TEXT,
  composite_figi TEXT,
  share_class_figi TEXT,
  ticker TEXT,
  name TEXT,
  exchange TEXT,
  asset_type TEXT,
  country TEXT,
  currency TEXT,
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS security_identifier_history (
  id UUID PRIMARY KEY,
  security_id UUID REFERENCES security_master(security_id),
  identifier_type TEXT NOT NULL,
  identifier_value TEXT NOT NULL,
  effective_from DATE,
  effective_to DATE,
  provider TEXT,
  confidence NUMERIC,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS security_sector_history (
  id UUID PRIMARY KEY,
  security_id UUID REFERENCES security_master(security_id),
  effective_from DATE,
  effective_to DATE,
  sector TEXT,
  industry_group TEXT,
  industry TEXT,
  sub_industry TEXT,
  provider TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fund_sector_allocations (
  id UUID PRIMARY KEY,
  fund_id UUID REFERENCES fund_master(fund_id),
  report_period DATE NOT NULL,
  sector TEXT NOT NULL,
  market_value_usd NUMERIC,
  weight NUMERIC,
  holdings_count INTEGER,
  source_filing_id UUID REFERENCES sec_filings(filing_id),
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(fund_id, report_period, sector)
);

CREATE TABLE IF NOT EXISTS security_prices_daily (
  security_id UUID REFERENCES security_master(security_id),
  price_date DATE NOT NULL,
  open NUMERIC,
  high NUMERIC,
  low NUMERIC,
  close NUMERIC,
  adjusted_close NUMERIC,
  volume NUMERIC,
  dividend_amount NUMERIC DEFAULT 0,
  split_factor NUMERIC DEFAULT 1,
  provider TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY(security_id, price_date, provider)
);

CREATE TABLE IF NOT EXISTS fund_return_series (
  id UUID PRIMARY KEY,
  fund_id UUID REFERENCES fund_master(fund_id),
  mode TEXT NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  rebalance_date DATE,
  return_value NUMERIC,
  cumulative_value NUMERIC,
  benchmark_return NUMERIC,
  benchmark_cumulative_value NUMERIC,
  data_confidence_score NUMERIC,
  methodology_version TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fund_return_metrics (
  id UUID PRIMARY KEY,
  fund_id UUID REFERENCES fund_master(fund_id),
  mode TEXT NOT NULL,
  period TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  cumulative_return NUMERIC,
  cagr NUMERIC,
  roic_proxy NUMERIC,
  alpha_vs_sp500 NUMERIC,
  beta_vs_sp500 NUMERIC,
  sharpe NUMERIC,
  sortino NUMERIC,
  max_drawdown NUMERIC,
  positive_quarter_rate NUMERIC,
  volatility_annualized NUMERIC,
  tracking_error NUMERIC,
  information_ratio NUMERIC,
  data_confidence_score NUMERIC,
  methodology_version TEXT,
  calculated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(fund_id, mode, period, as_of_date, methodology_version)
);

CREATE TABLE IF NOT EXISTS fund_leaderboard_snapshots (
  snapshot_id UUID PRIMARY KEY,
  as_of_date DATE NOT NULL,
  latest_report_period DATE,
  fund_id UUID REFERENCES fund_master(fund_id),
  mode TEXT NOT NULL,
  rank INTEGER,
  leaderboard_score NUMERIC,
  methodology_version TEXT,
  metrics_json JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_quality_checks (
  check_id UUID PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id UUID,
  check_name TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT NOT NULL,
  observed_value TEXT,
  expected_value TEXT,
  details JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);
