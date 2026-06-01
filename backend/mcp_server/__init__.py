"""
MCP Server — S&P 500 Market Data tools for the TradeTalk finance swarm.

Provides 5 tools accessible as FastAPI endpoints (mounted at /mcp/sp500/):
  - get_price_window
  - get_movement_context
  - get_causal_events
  - find_similar_events
  - get_gold_spx_context

Backend is switchable: DuckDB for local dev, BigQuery for production.
Set MCP_DATA_BACKEND=bigquery in production.
"""
