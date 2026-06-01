"""
Build movement_event_links and movement_context_daily for ALL (symbol, trade_date).

Links every daily price row to same-day, lagged, and macro events with
normalized attribution weights.

Usage:
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.mcp_server.build_movement_links
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.mcp_server.build_movement_links --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os

from .attribution import CATEGORY_LAG_CONFIG

logger = logging.getLogger(__name__)

# SQL fragment: category lag filter using CASE
_LAG_FILTER = """
    (
      (category = 'earnings' AND lag_days BETWEEN 0 AND 1)
      OR (category = 'fed_decision' AND lag_days BETWEEN 0 AND 1)
      OR (category = 'macro_data' AND lag_days BETWEEN 0 AND 2)
      OR (category = 'geopolitical' AND lag_days BETWEEN 0 AND 5)
      OR (category = 'tariff_policy' AND lag_days BETWEEN 0 AND 7)
      OR (category = 'insider_trade' AND lag_days BETWEEN 0 AND 3)
      OR (category = 'sec_filing' AND lag_days BETWEEN 0 AND 3)
      OR (category = 'news' AND lag_days BETWEEN 0 AND 3)
      OR (category = 'corporate_action' AND lag_days BETWEEN 0 AND 1)
      OR (category NOT IN (
          'earnings','fed_decision','macro_data','geopolitical','tariff_policy',
          'insider_trade','sec_filing','news','corporate_action'
        ) AND lag_days BETWEEN 0 AND 3)
    )
"""

MOVEMENT_LINKS_SQL = """
WITH all_days AS (
    SELECT symbol, trade_date, daily_return_pct
    FROM `{dataset}.daily_prices`
    WHERE close IS NOT NULL
),
symbol_events AS (
    SELECT
        d.symbol,
        d.trade_date AS move_date,
        e.event_id,
        e.category,
        DATE_DIFF(d.trade_date, DATE(e.published_at), DAY) AS lag_days,
        CASE
            WHEN DATE(e.published_at) = d.trade_date THEN 'same_day'
            ELSE 'lagged'
        END AS link_type,
        CASE
            WHEN DATE(e.published_at) = d.trade_date THEN 'regular'
            ELSE CAST(NULL AS STRING)
        END AS session_hint
    FROM all_days d
    JOIN `{dataset}.events_curated` e
      ON d.symbol IN UNNEST(e.affected_symbols)
    WHERE DATE(e.published_at) <= d.trade_date
      AND DATE(e.published_at) >= DATE_SUB(d.trade_date, INTERVAL 7 DAY)
      AND (
        DATE(e.published_at) < d.trade_date
        OR TIMESTAMP(e.published_at) <= TIMESTAMP(CONCAT(CAST(d.trade_date AS STRING), ' 21:00:00'))
      )
),
macro_events AS (
    SELECT
        d.symbol,
        d.trade_date AS move_date,
        m.event_id,
        m.category,
        DATE_DIFF(d.trade_date, DATE(m.published_at), DAY) AS lag_days,
        'macro' AS link_type,
        CAST(NULL AS STRING) AS session_hint
    FROM all_days d
    CROSS JOIN `{dataset}.macro_policy_events` m
    WHERE DATE(m.published_at) <= d.trade_date
      AND DATE(m.published_at) >= DATE_SUB(d.trade_date, INTERVAL 7 DAY)
),
combined AS (
    SELECT * FROM symbol_events
    UNION ALL
    SELECT * FROM macro_events
),
filtered AS (
    SELECT *,
        CASE
            WHEN link_type = 'same_day' THEN 1.5 / (1.0 + lag_days)
            ELSE 1.0 / (1.0 + lag_days)
        END AS raw_weight
    FROM combined
    WHERE {lag_filter}
),
normalized AS (
    SELECT
        symbol,
        move_date,
        event_id,
        link_type,
        lag_days,
        session_hint,
        category,
        raw_weight / NULLIF(SUM(raw_weight) OVER (PARTITION BY symbol, move_date), 0) AS attribution_weight
    FROM filtered
)
SELECT
    symbol,
    move_date,
    event_id,
    link_type,
    lag_days,
    attribution_weight,
    session_hint,
    category
FROM normalized
WHERE attribution_weight IS NOT NULL
"""

MOVEMENT_CONTEXT_SQL = """
CREATE OR REPLACE TABLE `{dataset}.movement_context_daily`
PARTITION BY trade_date
AS
WITH links AS (
    SELECT * FROM `{dataset}.movement_event_links`
),
events AS (
    SELECT event_id, headline, category, published_at
    FROM `{dataset}.events_curated`
    UNION ALL
    SELECT event_id, headline, category, published_at
    FROM `{dataset}.macro_policy_events`
),
joined AS (
    SELECT
        p.symbol,
        p.trade_date,
        p.close,
        p.volume,
        f.daily_return_pct,
        f.return_zscore_60d,
        f.relative_volume,
        f.market_regime,
        l.link_type,
        l.lag_days,
        l.attribution_weight,
        l.event_id,
        e.headline,
        e.category,
        e.published_at,
        g.spx_return,
        g.risk_regime
    FROM `{dataset}.daily_prices` p
    JOIN `{dataset}.daily_movement_features` f
      ON p.symbol = f.symbol AND p.trade_date = f.trade_date
    LEFT JOIN links l
      ON p.symbol = l.symbol AND p.trade_date = l.move_date
    LEFT JOIN events e ON l.event_id = e.event_id
    LEFT JOIN `{dataset}.gold_correlation_daily` g
      ON p.trade_date = g.trade_date
),
agg AS (
    SELECT
        symbol,
        trade_date,
        ANY_VALUE(close) AS close,
        ANY_VALUE(volume) AS volume,
        ANY_VALUE(daily_return_pct) AS daily_return_pct,
        ANY_VALUE(return_zscore_60d) AS return_zscore_60d,
        ANY_VALUE(relative_volume) AS relative_volume,
        ANY_VALUE(market_regime) AS market_regime,
        ANY_VALUE(spx_return) AS spx_return,
        ANY_VALUE(risk_regime) AS risk_regime,
        TO_JSON(ARRAY_AGG(
            IF(link_type = 'same_day', STRUCT(
                event_id, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS same_day_events_json,
        TO_JSON(ARRAY_AGG(
            IF(link_type = 'lagged', STRUCT(
                event_id, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS lagged_events_json,
        TO_JSON(ARRAY_AGG(
            IF(link_type = 'macro', STRUCT(
                event_id, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS macro_events_json,
        TO_JSON(ARRAY_AGG(
            IF(event_id IS NOT NULL, STRUCT(
                event_id, link_type, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS linked_events_json,
        MAX(IF(link_type IN ('same_day', 'lagged'), 1, 0)) AS has_symbol_event,
        MAX(IF(link_type = 'macro', 1, 0)) AS has_macro_event,
        ARRAY_AGG(
            IF(attribution_weight IS NOT NULL,
               STRUCT(attribution_weight, category, headline, link_type),
               NULL) IGNORE NULLS
            ORDER BY attribution_weight DESC LIMIT 1
        )[SAFE_OFFSET(0)] AS top_cause
    FROM joined
    GROUP BY symbol, trade_date
)
SELECT
    symbol,
    trade_date,
    close,
    volume,
    daily_return_pct,
    return_zscore_60d,
    relative_volume,
    market_regime,
    same_day_events_json,
    lagged_events_json,
    macro_events_json,
    linked_events_json,
    CASE
        WHEN has_symbol_event = 1 THEN 'symbol_specific'
        WHEN has_macro_event = 1 THEN 'macro_only'
        ELSE 'no_catalyst'
    END AS catalyst_status,
    top_cause.category AS primary_cause_category,
    top_cause.headline AS primary_cause_headline,
    top_cause.attribution_weight AS primary_cause_weight,
    spx_return,
    risk_regime
FROM agg
"""

# Backfill price_movement_attributions from links (backward compat)
ATTRIBUTIONS_FROM_LINKS_SQL = """
CREATE OR REPLACE TABLE `{dataset}.price_movement_attributions`
PARTITION BY move_date
AS
SELECT
    GENERATE_UUID() AS attribution_id,
    l.symbol,
    l.move_date,
    p.daily_return_pct AS move_return_pct,
    l.event_id,
    l.attribution_weight,
    l.lag_days,
    l.category
FROM `{dataset}.movement_event_links` l
JOIN `{dataset}.daily_prices` p
  ON l.symbol = p.symbol AND l.move_date = p.trade_date
WHERE l.link_type IN ('same_day', 'lagged')
"""


def seed_category_lag_config(dry_run: bool = False) -> None:
    """Seed category_lag_config table from CATEGORY_LAG_CONFIG."""
    rows = [
        {"category": cat, "min_lag": cfg["min_lag"], "max_lag": cfg["max_lag"]}
        for cat, cfg in CATEGORY_LAG_CONFIG.items()
    ]
    if dry_run:
        logger.info("[MovementLinks] Would seed %d category_lag_config rows", len(rows))
        return

    import os
    if os.environ.get("MCP_DATA_BACKEND", "duckdb") != "bigquery":
        return

    from .backend import backend
    from .bq_schema import FULL_DATASET

    try:
        backend().execute(f"TRUNCATE TABLE `{FULL_DATASET}.category_lag_config`")
    except Exception:
        pass
    backend().insert_rows("category_lag_config", rows)
    logger.info("[MovementLinks] Seeded category_lag_config")


def run_movement_links_job(dry_run: bool = False) -> str:
    backend_type = os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()

    if backend_type != "bigquery":
        logger.warning("[MovementLinks] Requires MCP_DATA_BACKEND=bigquery")
        return "skipped"

    from .backend import backend
    from .bq_schema import FULL_DATASET

    links_sql = MOVEMENT_LINKS_SQL.format(
        dataset=FULL_DATASET,
        lag_filter=_LAG_FILTER,
    )

    if dry_run:
        logger.info("[MovementLinks] Would run links SQL (%d chars)", len(links_sql))
        logger.info("[MovementLinks] Would materialize movement_context_daily")
        return "dry_run"

    seed_category_lag_config(dry_run=False)

    rebuild_links = f"""
        CREATE OR REPLACE TABLE `{FULL_DATASET}.movement_event_links`
        PARTITION BY move_date
        AS
        {links_sql}
    """
    backend().execute(rebuild_links)
    logger.info("[MovementLinks] Built movement_event_links")

    context_sql = MOVEMENT_CONTEXT_SQL.format(dataset=FULL_DATASET)
    backend().execute(context_sql)
    logger.info("[MovementLinks] Built movement_context_daily")

    attr_sql = ATTRIBUTIONS_FROM_LINKS_SQL.format(dataset=FULL_DATASET)
    try:
        backend().execute(attr_sql)
        logger.info("[MovementLinks] Refreshed price_movement_attributions")
    except Exception as e:
        logger.warning("[MovementLinks] Attributions refresh skipped: %s", e)

    return "done"


INCREMENTAL_CONTEXT_SQL = """
INSERT INTO `{dataset}.movement_context_daily` (
    symbol, trade_date, close, volume, daily_return_pct, return_zscore_60d,
    relative_volume, market_regime, same_day_events_json, lagged_events_json,
    macro_events_json, linked_events_json, catalyst_status,
    primary_cause_category, primary_cause_headline, primary_cause_weight,
    spx_return, risk_regime
)
WITH links AS (
    SELECT * FROM `{dataset}.movement_event_links`
    WHERE move_date >= DATE '{start_date}' AND move_date <= DATE '{end_date}'
),
events AS (
    SELECT event_id, headline, category, published_at
    FROM `{dataset}.events_curated`
    UNION ALL
    SELECT event_id, headline, category, published_at
    FROM `{dataset}.macro_policy_events`
),
joined AS (
    SELECT
        p.symbol,
        p.trade_date,
        p.close,
        p.volume,
        f.daily_return_pct,
        f.return_zscore_60d,
        f.relative_volume,
        f.market_regime,
        l.link_type,
        l.lag_days,
        l.attribution_weight,
        l.event_id,
        e.headline,
        e.category,
        e.published_at,
        g.spx_return,
        g.risk_regime
    FROM `{dataset}.daily_prices` p
    JOIN `{dataset}.daily_movement_features` f
      ON p.symbol = f.symbol AND p.trade_date = f.trade_date
    LEFT JOIN links l
      ON p.symbol = l.symbol AND p.trade_date = l.move_date
    LEFT JOIN events e ON l.event_id = e.event_id
    LEFT JOIN `{dataset}.gold_correlation_daily` g
      ON p.trade_date = g.trade_date
    WHERE p.trade_date >= DATE '{start_date}' AND p.trade_date <= DATE '{end_date}'
      AND p.close IS NOT NULL
),
agg AS (
    SELECT
        symbol,
        trade_date,
        ANY_VALUE(close) AS close,
        ANY_VALUE(volume) AS volume,
        ANY_VALUE(daily_return_pct) AS daily_return_pct,
        ANY_VALUE(return_zscore_60d) AS return_zscore_60d,
        ANY_VALUE(relative_volume) AS relative_volume,
        ANY_VALUE(market_regime) AS market_regime,
        ANY_VALUE(spx_return) AS spx_return,
        ANY_VALUE(risk_regime) AS risk_regime,
        TO_JSON(ARRAY_AGG(
            IF(link_type = 'same_day', STRUCT(
                event_id, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS same_day_events_json,
        TO_JSON(ARRAY_AGG(
            IF(link_type = 'lagged', STRUCT(
                event_id, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS lagged_events_json,
        TO_JSON(ARRAY_AGG(
            IF(link_type = 'macro', STRUCT(
                event_id, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS macro_events_json,
        TO_JSON(ARRAY_AGG(
            IF(event_id IS NOT NULL, STRUCT(
                event_id, link_type, lag_days, attribution_weight,
                headline, category, CAST(published_at AS STRING) AS published_at
            ), NULL) IGNORE NULLS
        )) AS linked_events_json,
        MAX(IF(link_type IN ('same_day', 'lagged'), 1, 0)) AS has_symbol_event,
        MAX(IF(link_type = 'macro', 1, 0)) AS has_macro_event,
        ARRAY_AGG(
            IF(attribution_weight IS NOT NULL,
               STRUCT(attribution_weight, category, headline, link_type),
               NULL) IGNORE NULLS
            ORDER BY attribution_weight DESC LIMIT 1
        )[SAFE_OFFSET(0)] AS top_cause
    FROM joined
    GROUP BY symbol, trade_date
)
SELECT
    symbol,
    trade_date,
    close,
    volume,
    daily_return_pct,
    return_zscore_60d,
    relative_volume,
    market_regime,
    same_day_events_json,
    lagged_events_json,
    macro_events_json,
    linked_events_json,
    CASE
        WHEN has_symbol_event = 1 THEN 'symbol_specific'
        WHEN has_macro_event = 1 THEN 'macro_only'
        ELSE 'no_catalyst'
    END AS catalyst_status,
    top_cause.category AS primary_cause_category,
    top_cause.headline AS primary_cause_headline,
    top_cause.attribution_weight AS primary_cause_weight,
    spx_return,
    risk_regime
FROM agg
"""


def run_incremental_movement_links(
    start_date: str,
    end_date: str,
    dry_run: bool = False,
) -> dict:
    """
    Refresh movement_event_links and movement_context_daily for a date window only.
    Deletes existing rows in the window, then inserts fresh links/context.
    """
    backend_type = os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()
    if backend_type != "bigquery":
        return {"status": "skipped", "reason": "requires bigquery"}

    from .backend import backend
    from .bq_schema import FULL_DATASET

    links_sql = MOVEMENT_LINKS_SQL.format(
        dataset=FULL_DATASET,
        lag_filter=_LAG_FILTER,
    )
    links_sql = links_sql.replace(
        "WHERE close IS NOT NULL",
        f"WHERE close IS NOT NULL"
        f" AND trade_date >= DATE '{start_date}'"
        f" AND trade_date <= DATE '{end_date}'",
    )

    if dry_run:
        logger.info(
            "[MovementLinks] Would incrementally rebuild %s → %s",
            start_date,
            end_date,
        )
        return {"status": "dry_run", "start_date": start_date, "end_date": end_date}

    backend().execute(
        f"DELETE FROM `{FULL_DATASET}.movement_event_links` "
        f"WHERE move_date >= DATE '{start_date}' AND move_date <= DATE '{end_date}'"
    )
    insert_links = f"""
        INSERT INTO `{FULL_DATASET}.movement_event_links`
        (symbol, move_date, event_id, link_type, lag_days, attribution_weight,
         session_hint, category)
        {links_sql}
    """
    backend().execute(insert_links)
    logger.info("[MovementLinks] Incremental links %s → %s", start_date, end_date)

    backend().execute(
        f"DELETE FROM `{FULL_DATASET}.movement_context_daily` "
        f"WHERE trade_date >= DATE '{start_date}' AND trade_date <= DATE '{end_date}'"
    )
    context_sql = INCREMENTAL_CONTEXT_SQL.format(
        dataset=FULL_DATASET,
        start_date=start_date,
        end_date=end_date,
    )
    backend().execute(context_sql)
    logger.info("[MovementLinks] Incremental context %s → %s", start_date, end_date)

    return {"status": "done", "start_date": start_date, "end_date": end_date}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Build movement-event links")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--incremental", action="store_true",
                        help="Refresh only a date window (requires --start and --end)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    args = parser.parse_args()
    if args.incremental:
        if not args.start or not args.end:
            parser.error("--incremental requires --start and --end")
        result = run_incremental_movement_links(
            start_date=args.start,
            end_date=args.end,
            dry_run=args.dry_run,
        )
    else:
        result = run_movement_links_job(dry_run=args.dry_run)
    print(f"Result: {result}")


if __name__ == "__main__":
    main()
