"""
Price movement attribution — links big-move days to causal events.

Uses category-specific lag windows and decay-weighted attribution.
Runs nightly after events_curated and daily_prices are updated.

Usage:
    python -m backend.mcp_server.attribution --dry-run
    python -m backend.mcp_server.attribution
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)

CATEGORY_LAG_CONFIG: Dict[str, Dict[str, int]] = {
    "earnings": {"min_lag": 0, "max_lag": 1},
    "fed_decision": {"min_lag": 0, "max_lag": 1},
    "macro_data": {"min_lag": 0, "max_lag": 2},
    "geopolitical": {"min_lag": 0, "max_lag": 5},
    "tariff_policy": {"min_lag": 0, "max_lag": 7},
    "insider_trade": {"min_lag": 0, "max_lag": 3},
}

BIG_MOVE_ZSCORE_THRESHOLD = 1.5
BIG_MOVE_RETURN_THRESHOLD = 2.0

ATTRIBUTION_SQL_BQ = """
WITH big_moves AS (
    SELECT symbol, trade_date, daily_return_pct, return_zscore_60d
    FROM {dataset}.daily_movement_features
    WHERE ABS(return_zscore_60d) > {zscore_thresh}
       OR ABS(daily_return_pct) > {return_thresh}
),
candidate_events AS (
    SELECT
        bm.symbol,
        bm.trade_date AS move_date,
        bm.daily_return_pct AS move_return_pct,
        e.event_id,
        e.category,
        DATE_DIFF(bm.trade_date, DATE(e.published_at), DAY) AS lag_days
    FROM big_moves bm
    CROSS JOIN {dataset}.events_curated e
    WHERE (
        bm.symbol IN UNNEST(e.affected_symbols)
        OR ARRAY_LENGTH(e.affected_symbols) = 0
    )
    AND DATE(e.published_at) <= bm.trade_date
    AND DATE(e.published_at) >= DATE_SUB(bm.trade_date, INTERVAL 7 DAY)
),
filtered AS (
    SELECT *,
        1.0 / (1.0 + lag_days) AS raw_weight
    FROM candidate_events
    WHERE (category = 'earnings' AND lag_days BETWEEN 0 AND 1)
       OR (category = 'fed_decision' AND lag_days BETWEEN 0 AND 1)
       OR (category = 'macro_data' AND lag_days BETWEEN 0 AND 2)
       OR (category = 'geopolitical' AND lag_days BETWEEN 0 AND 5)
       OR (category = 'tariff_policy' AND lag_days BETWEEN 0 AND 7)
       OR (category = 'insider_trade' AND lag_days BETWEEN 0 AND 3)
       OR (category NOT IN ('earnings','fed_decision','macro_data','geopolitical','tariff_policy','insider_trade')
           AND lag_days BETWEEN 0 AND 3)
),
normalized AS (
    SELECT *,
        raw_weight / SUM(raw_weight) OVER (PARTITION BY symbol, move_date) AS attribution_weight
    FROM filtered
)
SELECT
    GENERATE_UUID() AS attribution_id,
    symbol,
    move_date,
    move_return_pct,
    event_id,
    attribution_weight,
    lag_days,
    category
FROM normalized
"""

ATTRIBUTION_SQL_DUCKDB = """
CREATE OR REPLACE TABLE price_movement_attributions AS
WITH big_moves AS (
    SELECT symbol, trade_date, daily_return_pct, return_zscore_60d
    FROM daily_movement_features
    WHERE ABS(return_zscore_60d) > {zscore_thresh}
       OR ABS(daily_return_pct) > {return_thresh}
),
candidate_events AS (
    SELECT
        bm.symbol,
        bm.trade_date AS move_date,
        bm.daily_return_pct AS move_return_pct,
        e.event_id,
        e.category,
        DATEDIFF('day', CAST(e.published_at AS DATE), bm.trade_date) AS lag_days
    FROM big_moves bm
    CROSS JOIN events_curated e
    WHERE CAST(e.published_at AS DATE) <= bm.trade_date
      AND CAST(e.published_at AS DATE) >= bm.trade_date - INTERVAL 7 DAY
),
filtered AS (
    SELECT *,
        1.0 / (1.0 + lag_days) AS raw_weight
    FROM candidate_events
    WHERE (category = 'earnings' AND lag_days BETWEEN 0 AND 1)
       OR (category = 'fed_decision' AND lag_days BETWEEN 0 AND 1)
       OR (category = 'macro_data' AND lag_days BETWEEN 0 AND 2)
       OR (category = 'geopolitical' AND lag_days BETWEEN 0 AND 5)
       OR (category = 'tariff_policy' AND lag_days BETWEEN 0 AND 7)
       OR (category = 'insider_trade' AND lag_days BETWEEN 0 AND 3)
       OR (category NOT IN ('earnings','fed_decision','macro_data','geopolitical','tariff_policy','insider_trade')
           AND lag_days BETWEEN 0 AND 3)
),
normalized AS (
    SELECT *,
        raw_weight / SUM(raw_weight) OVER (PARTITION BY symbol, move_date) AS attribution_weight
    FROM filtered
)
SELECT
    uuid() AS attribution_id,
    symbol,
    move_date,
    move_return_pct,
    event_id,
    attribution_weight,
    lag_days,
    category
FROM normalized
"""


def run_attribution_job(dry_run: bool = False) -> str:
    """Run the attribution job — links big moves to causal events."""
    backend_type = os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()

    if backend_type == "bigquery":
        from .bq_schema import FULL_DATASET
        sql = ATTRIBUTION_SQL_BQ.format(
            dataset=FULL_DATASET,
            zscore_thresh=BIG_MOVE_ZSCORE_THRESHOLD,
            return_thresh=BIG_MOVE_RETURN_THRESHOLD,
        )
        if dry_run:
            logger.info("[Attribution] Would run BigQuery job:\n%s", sql[:500])
            return "dry_run"

        rebuild_sql = f"""
            CREATE OR REPLACE TABLE `{FULL_DATASET}.price_movement_attributions`
            PARTITION BY move_date
            AS
            {sql}
        """
        from .backend import backend
        backend().execute(rebuild_sql)
        logger.info("[Attribution] Completed BigQuery attribution job")
        return "bigquery_done"
    else:
        sql = ATTRIBUTION_SQL_DUCKDB.format(
            zscore_thresh=BIG_MOVE_ZSCORE_THRESHOLD,
            return_thresh=BIG_MOVE_RETURN_THRESHOLD,
        )
        if dry_run:
            logger.info("[Attribution] Would run DuckDB job")
            return "dry_run"
        from .backend import backend
        try:
            backend().query(sql)
            logger.info("[Attribution] Completed DuckDB attribution job")
            return "duckdb_done"
        except Exception as e:
            logger.warning("[Attribution] DuckDB job failed (events_curated may not exist): %s", e)
            return "skipped_no_events"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    result = run_attribution_job(dry_run=dry)
    print(f"Result: {result}")
