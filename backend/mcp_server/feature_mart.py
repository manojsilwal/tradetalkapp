"""
Feature mart computation — nightly materialization of daily_movement_features.

For BigQuery: runs as a scheduled query.
For DuckDB: runs in-process from daily_pipeline.

Usage:
    python -m backend.mcp_server.feature_mart --dry-run
    python -m backend.mcp_server.feature_mart
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

FEATURE_MART_SQL = """
CREATE OR REPLACE TABLE `{dataset}.daily_movement_features`
PARTITION BY trade_date
AS
WITH base AS (
    SELECT
        symbol,
        trade_date,
        close,
        daily_return_pct,
        relative_volume,
        ma_20,
        ma_50,
        ma_200,
        AVG(daily_return_pct) OVER (
            PARTITION BY symbol ORDER BY trade_date ROWS 60 PRECEDING
        ) AS avg_return_60d,
        STDDEV(daily_return_pct) OVER (
            PARTITION BY symbol ORDER BY trade_date ROWS 60 PRECEDING
        ) AS std_return_60d
    FROM {dataset}.daily_prices
    WHERE close IS NOT NULL AND daily_return_pct IS NOT NULL
),
features AS (
    SELECT
        symbol,
        trade_date,
        close,
        daily_return_pct,
        CASE
            WHEN std_return_60d IS NOT NULL AND std_return_60d > 0
            THEN (daily_return_pct - avg_return_60d) / std_return_60d
            ELSE NULL
        END AS return_zscore_60d,
        relative_volume,
        CASE
            WHEN ma_20 IS NOT NULL AND ma_50 IS NOT NULL AND ma_20 > ma_50
            THEN 'bullish'
            ELSE 'bearish'
        END AS trend_20_50,
        PERCENT_RANK() OVER (
            PARTITION BY trade_date ORDER BY daily_return_pct
        ) AS sector_rank_pct
    FROM base
)
SELECT
    f.symbol,
    f.trade_date,
    f.close,
    f.daily_return_pct,
    f.return_zscore_60d,
    f.relative_volume,
    f.trend_20_50,
    f.sector_rank_pct,
    CAST(NULL AS STRING) AS market_regime,
    CAST(NULL AS STRING) AS top_event_id
FROM features f
"""

FEATURE_MART_SQL_DUCKDB = """
CREATE OR REPLACE TABLE daily_movement_features AS
WITH base AS (
    SELECT
        symbol,
        trade_date,
        close,
        daily_return_pct,
        relative_volume,
        ma_20,
        ma_50,
        ma_200,
        AVG(daily_return_pct) OVER (
            PARTITION BY symbol ORDER BY trade_date ROWS 60 PRECEDING
        ) AS avg_return_60d,
        STDDEV(daily_return_pct) OVER (
            PARTITION BY symbol ORDER BY trade_date ROWS 60 PRECEDING
        ) AS std_return_60d
    FROM daily_prices
    WHERE close IS NOT NULL AND daily_return_pct IS NOT NULL
),
features AS (
    SELECT
        symbol,
        trade_date,
        close,
        daily_return_pct,
        CASE
            WHEN std_return_60d IS NOT NULL AND std_return_60d > 0
            THEN (daily_return_pct - avg_return_60d) / std_return_60d
            ELSE NULL
        END AS return_zscore_60d,
        relative_volume,
        CASE
            WHEN ma_20 IS NOT NULL AND ma_50 IS NOT NULL AND ma_20 > ma_50
            THEN 'bullish'
            ELSE 'bearish'
        END AS trend_20_50,
        PERCENT_RANK() OVER (
            PARTITION BY trade_date ORDER BY daily_return_pct
        ) AS sector_rank_pct
    FROM base
)
SELECT
    f.symbol,
    f.trade_date,
    f.close,
    f.daily_return_pct,
    f.return_zscore_60d,
    f.relative_volume,
    f.trend_20_50,
    f.sector_rank_pct,
    NULL AS market_regime,
    NULL AS top_event_id
FROM features f
"""


def refresh_feature_mart(dry_run: bool = False) -> str:
    """Run the feature mart materialization."""
    backend_type = os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()

    if backend_type == "bigquery":
        from .bq_schema import FULL_DATASET
        sql = FEATURE_MART_SQL.format(dataset=FULL_DATASET)
        if dry_run:
            logger.info("[Feature Mart] Would execute BigQuery DDL:\n%s", sql[:500])
            return "dry_run"
        from .backend import backend
        backend().execute(sql)
        logger.info("[Feature Mart] Refreshed daily_movement_features in BigQuery")
        return "bigquery_refreshed"
    else:
        if dry_run:
            logger.info("[Feature Mart] Would execute DuckDB DDL")
            return "dry_run"
        from .backend import backend
        backend().query(FEATURE_MART_SQL_DUCKDB)
        logger.info("[Feature Mart] Refreshed daily_movement_features in DuckDB")
        return "duckdb_refreshed"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    result = refresh_feature_mart(dry_run=dry)
    print(f"Result: {result}")
