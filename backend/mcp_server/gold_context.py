"""
Gold-SPX correlation context — materialized daily table.

Provides the Gold Analysis Agent with equity correlation, risk regime,
and DXY/real-yield context in a single row read.

Usage:
    python -m backend.mcp_server.gold_context --dry-run
    python -m backend.mcp_server.gold_context
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

GOLD_CONTEXT_SQL_BQ = """
CREATE OR REPLACE TABLE `{dataset}.gold_correlation_daily`
PARTITION BY trade_date
AS
WITH spx AS (
    SELECT trade_date, daily_return_pct AS spx_return
    FROM {dataset}.daily_prices
    WHERE symbol = 'SPY'
),
xau AS (
    SELECT trade_date, daily_return_pct AS xau_return
    FROM {dataset}.daily_prices
    WHERE symbol = 'GLD'
),
dxy AS (
    SELECT trade_date, daily_return_pct AS dxy_return
    FROM {dataset}.daily_prices
    WHERE symbol = 'UUP'
),
joined AS (
    SELECT
        s.trade_date,
        s.spx_return,
        x.xau_return,
        d.dxy_return,
        CORR(s.spx_return, x.xau_return) OVER (
            ORDER BY s.trade_date ROWS 30 PRECEDING
        ) AS spx_xau_corr_30d
    FROM spx s
    LEFT JOIN xau x ON s.trade_date = x.trade_date
    LEFT JOIN dxy d ON s.trade_date = d.trade_date
    WHERE x.xau_return IS NOT NULL
)
SELECT
    trade_date,
    spx_return,
    xau_return,
    dxy_return,
    spx_xau_corr_30d,
    CASE
        WHEN spx_return > 0.5 AND xau_return < -0.3 THEN 'risk_on'
        WHEN spx_return < -1.0 AND xau_return > 0.5 THEN 'stress'
        WHEN ABS(spx_xau_corr_30d) < 0.1 THEN 'decorrelated'
        ELSE 'neutral'
    END AS risk_regime,
    CAST(NULL AS FLOAT64) AS real_yield_10y
FROM joined
"""

GOLD_CONTEXT_SQL_DUCKDB = """
CREATE OR REPLACE TABLE gold_correlation_daily AS
WITH spx AS (
    SELECT trade_date, daily_return_pct AS spx_return
    FROM daily_prices
    WHERE symbol = 'SPY'
),
xau AS (
    SELECT trade_date, daily_return_pct AS xau_return
    FROM daily_prices
    WHERE symbol = 'GLD'
),
dxy AS (
    SELECT trade_date, daily_return_pct AS dxy_return
    FROM daily_prices
    WHERE symbol = 'UUP'
),
joined AS (
    SELECT
        s.trade_date,
        s.spx_return,
        x.xau_return,
        d.dxy_return,
        CORR(s.spx_return, x.xau_return) OVER (
            ORDER BY s.trade_date ROWS 30 PRECEDING
        ) AS spx_xau_corr_30d
    FROM spx s
    LEFT JOIN xau x ON s.trade_date = x.trade_date
    LEFT JOIN dxy d ON s.trade_date = d.trade_date
    WHERE x.xau_return IS NOT NULL
)
SELECT
    trade_date,
    spx_return,
    xau_return,
    dxy_return,
    spx_xau_corr_30d,
    CASE
        WHEN spx_return > 0.5 AND xau_return < -0.3 THEN 'risk_on'
        WHEN spx_return < -1.0 AND xau_return > 0.5 THEN 'stress'
        WHEN ABS(spx_xau_corr_30d) < 0.1 THEN 'decorrelated'
        ELSE 'neutral'
    END AS risk_regime,
    NULL AS real_yield_10y
FROM joined
"""


def refresh_gold_context(dry_run: bool = False) -> str:
    """Refresh the gold_correlation_daily table."""
    backend_type = os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()

    if backend_type == "bigquery":
        from .bq_schema import FULL_DATASET
        sql = GOLD_CONTEXT_SQL_BQ.format(dataset=FULL_DATASET)
        if dry_run:
            logger.info("[Gold Context] Would execute BigQuery DDL")
            return "dry_run"
        from .backend import backend
        backend().execute(sql)
        logger.info("[Gold Context] Refreshed gold_correlation_daily in BigQuery")
        return "bigquery_refreshed"
    else:
        if dry_run:
            logger.info("[Gold Context] Would execute DuckDB DDL")
            return "dry_run"
        from .backend import backend
        try:
            backend().query(GOLD_CONTEXT_SQL_DUCKDB)
            logger.info("[Gold Context] Refreshed gold_correlation_daily in DuckDB")
            return "duckdb_refreshed"
        except Exception as e:
            logger.warning("[Gold Context] DuckDB refresh failed (GLD/UUP may not be in data lake): %s", e)
            return "skipped"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    result = refresh_gold_context(dry_run=dry)
    print(f"Result: {result}")
