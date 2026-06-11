-- Migration 003: Add api_url column to llm_api_calls
ALTER TABLE llm_api_calls ADD COLUMN api_url TEXT;
