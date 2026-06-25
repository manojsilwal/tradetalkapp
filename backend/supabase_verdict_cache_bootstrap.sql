-- One-time bootstrap for persisted decision-terminal verdict cache (VERDICT_CACHE_BACKEND=supabase).
-- Run in Supabase Dashboard → SQL Editor.

CREATE TABLE IF NOT EXISTS public.verdict_cache (
  ticker TEXT NOT NULL,
  session_date DATE NOT NULL,
  slice TEXT NOT NULL DEFAULT 'verdict',
  payload_json JSONB NOT NULL,
  verdict_captured_at_utc TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (ticker, session_date, slice)
);

CREATE INDEX IF NOT EXISTS idx_verdict_cache_session_date ON public.verdict_cache (session_date DESC);
