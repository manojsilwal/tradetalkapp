"""
BigQuery schema definitions and table creation for tradetalk_swarm dataset.

Run standalone to create/update tables:
    python -m backend.mcp_server.bq_schema
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "tradetalkapp-492904")
DATASET_ID = os.environ.get("BQ_DATASET_ID", "tradetalk_swarm")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "tradetalk-data-lake")

FULL_DATASET = f"{PROJECT_ID}.{DATASET_ID}"

TABLE_SCHEMAS = {
    "daily_prices": [
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "trade_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "open", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "high", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "low", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "close", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "volume", "type": "INT64", "mode": "NULLABLE"},
        {"name": "daily_return_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "ma_20", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "ma_50", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "ma_200", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "relative_volume", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "ingested_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    ],
    "events_curated": [
        {"name": "event_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "published_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "category", "type": "STRING", "mode": "REQUIRED"},
        {"name": "source", "type": "STRING", "mode": "NULLABLE"},
        {"name": "headline", "type": "STRING", "mode": "NULLABLE"},
        {"name": "body_text", "type": "STRING", "mode": "NULLABLE"},
        {"name": "affected_symbols", "type": "STRING", "mode": "REPEATED"},
        {"name": "sentiment_score", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "dedupe_cluster_id", "type": "STRING", "mode": "NULLABLE"},
        {"name": "embedding_id", "type": "STRING", "mode": "NULLABLE"},
    ],
    "pipeline_snapshots": [
        {"name": "snapshot_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "snapshot_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "snapshot_type", "type": "STRING", "mode": "REQUIRED"},
        {"name": "payload_json", "type": "JSON", "mode": "NULLABLE"},
        {"name": "summary_text", "type": "STRING", "mode": "NULLABLE"},
        {"name": "created_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    ],
    "agent_learnings": [
        {"name": "learning_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "agent_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "learning_type", "type": "STRING", "mode": "REQUIRED"},
        {"name": "observation", "type": "STRING", "mode": "NULLABLE"},
        {"name": "market_regime", "type": "STRING", "mode": "NULLABLE"},
        {"name": "feature_context_json", "type": "JSON", "mode": "NULLABLE"},
        {"name": "created_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
        {"name": "source_pipeline_run", "type": "STRING", "mode": "NULLABLE"},
    ],
    "daily_movement_features": [
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "trade_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "close", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "daily_return_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "return_zscore_60d", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "relative_volume", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "trend_20_50", "type": "STRING", "mode": "NULLABLE"},
        {"name": "sector_rank_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "market_regime", "type": "STRING", "mode": "NULLABLE"},
        {"name": "top_event_id", "type": "STRING", "mode": "NULLABLE"},
    ],
    "price_movement_attributions": [
        {"name": "attribution_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "move_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "move_return_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "event_id", "type": "STRING", "mode": "NULLABLE"},
        {"name": "attribution_weight", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "lag_days", "type": "INT64", "mode": "NULLABLE"},
        {"name": "category", "type": "STRING", "mode": "NULLABLE"},
    ],
    "gold_correlation_daily": [
        {"name": "trade_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "spx_return", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "xau_return", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "dxy_return", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "spx_xau_corr_30d", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "risk_regime", "type": "STRING", "mode": "NULLABLE"},
        {"name": "real_yield_10y", "type": "FLOAT64", "mode": "NULLABLE"},
    ],
    "index_membership": [
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "index_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "added_date", "type": "DATE", "mode": "NULLABLE"},
        {"name": "removed_date", "type": "DATE", "mode": "NULLABLE"},
        {"name": "sector_at_add", "type": "STRING", "mode": "NULLABLE"},
    ],
    "macro_policy_events": [
        {"name": "event_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "published_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "category", "type": "STRING", "mode": "REQUIRED"},
        {"name": "headline", "type": "STRING", "mode": "NULLABLE"},
        {"name": "body_text", "type": "STRING", "mode": "NULLABLE"},
        {"name": "affected_symbols", "type": "STRING", "mode": "REPEATED"},
        {"name": "source", "type": "STRING", "mode": "NULLABLE"},
    ],
    "category_lag_config": [
        {"name": "category", "type": "STRING", "mode": "REQUIRED"},
        {"name": "min_lag", "type": "INT64", "mode": "REQUIRED"},
        {"name": "max_lag", "type": "INT64", "mode": "REQUIRED"},
    ],
    "movement_event_links": [
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "move_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "event_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "link_type", "type": "STRING", "mode": "REQUIRED"},
        {"name": "lag_days", "type": "INT64", "mode": "NULLABLE"},
        {"name": "attribution_weight", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "session_hint", "type": "STRING", "mode": "NULLABLE"},
        {"name": "category", "type": "STRING", "mode": "NULLABLE"},
    ],
    "movement_context_daily": [
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "trade_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "close", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "volume", "type": "INT64", "mode": "NULLABLE"},
        {"name": "daily_return_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "return_zscore_60d", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "relative_volume", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "market_regime", "type": "STRING", "mode": "NULLABLE"},
        {"name": "same_day_events_json", "type": "JSON", "mode": "NULLABLE"},
        {"name": "lagged_events_json", "type": "JSON", "mode": "NULLABLE"},
        {"name": "macro_events_json", "type": "JSON", "mode": "NULLABLE"},
        {"name": "linked_events_json", "type": "JSON", "mode": "NULLABLE"},
        {"name": "catalyst_status", "type": "STRING", "mode": "NULLABLE"},
        {"name": "primary_cause_category", "type": "STRING", "mode": "NULLABLE"},
        {"name": "primary_cause_headline", "type": "STRING", "mode": "NULLABLE"},
        {"name": "primary_cause_weight", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "spx_return", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "risk_regime", "type": "STRING", "mode": "NULLABLE"},
    ],
    "daily_brief_snapshot": [
        {"name": "trade_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "bucket", "type": "STRING", "mode": "REQUIRED"},
        {"name": "rank", "type": "INT64", "mode": "REQUIRED"},
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "daily_return_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "close", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "volume", "type": "INT64", "mode": "NULLABLE"},
        {"name": "relative_volume", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "return_zscore_60d", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "market_regime", "type": "STRING", "mode": "NULLABLE"},
        {"name": "catalyst_status", "type": "STRING", "mode": "NULLABLE"},
        {"name": "primary_cause_category", "type": "STRING", "mode": "NULLABLE"},
        {"name": "primary_cause_headline", "type": "STRING", "mode": "NULLABLE"},
        {"name": "primary_cause_weight", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "verdict", "type": "STRING", "mode": "NULLABLE"},
        {"name": "one_line_reason", "type": "STRING", "mode": "NULLABLE"},
        {"name": "adjustment_note", "type": "STRING", "mode": "NULLABLE"},
        {"name": "verdict_tier", "type": "STRING", "mode": "NULLABLE"},
        {"name": "scorecard_signal", "type": "STRING", "mode": "NULLABLE"},
        {"name": "scorecard_ratio", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "valuation_pct_vs_fair", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "is_compelling", "type": "BOOL", "mode": "NULLABLE"},
        {"name": "preset", "type": "STRING", "mode": "NULLABLE"},
        {"name": "revenue_growth_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "eps_growth_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "dividend_yield_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "debt_to_equity", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "beta", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "updated_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    ],
    "rag_price_facts": [
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "trade_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "daily_return_pct", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "return_zscore_60d", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "relative_volume", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "close", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "volume", "type": "INT64", "mode": "NULLABLE"},
        {"name": "ingested_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    ],
    "rag_macro_facts": [
        {"name": "release_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "release_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "actual_value", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "consensus_value", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "prior_value", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "surprise_sign", "type": "STRING", "mode": "NULLABLE"},
        {"name": "ingested_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    ],
    "rag_flow_snapshots": [
        {"name": "flow_date", "type": "DATE", "mode": "REQUIRED"},
        {"name": "opening_capital_total_usd", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "closing_capital_total_usd", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "net_capital_change_usd", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "reconciliation_gap_usd", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "is_reconciled", "type": "BOOL", "mode": "NULLABLE"},
        {"name": "us_net_increased", "type": "BOOL", "mode": "NULLABLE"},
        {"name": "tolerance_usd", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "raw_payload_json", "type": "STRING", "mode": "NULLABLE"},
        {"name": "ingested_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    ],
    "rag_symbol_interest": [
        {"name": "symbol", "type": "STRING", "mode": "REQUIRED"},
        {"name": "search_count", "type": "INT64", "mode": "REQUIRED"},
        {"name": "last_searched", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "distinct_users_json", "type": "STRING", "mode": "NULLABLE"},
    ],
    "rag_ingestion_log": [
        {"name": "candidate_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "source_type", "type": "STRING", "mode": "REQUIRED"},
        {"name": "triggered_by", "type": "STRING", "mode": "REQUIRED"},
        {"name": "symbols", "type": "STRING", "mode": "REPEATED"},
        {"name": "as_of_ts", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "decision", "type": "STRING", "mode": "REQUIRED"},
        {"name": "decision_reason", "type": "STRING", "mode": "NULLABLE"},
        {"name": "keep_as", "type": "STRING", "mode": "NULLABLE"},
        {"name": "raw_payload_ref", "type": "STRING", "mode": "NULLABLE"},
        {"name": "agent_version", "type": "STRING", "mode": "NULLABLE"},
        {"name": "model_version", "type": "STRING", "mode": "NULLABLE"},
        {"name": "created_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    ],
}

PARTITION_CONFIG = {
    "daily_prices": {"field": "trade_date", "type": "DAY"},
    "events_curated": {"field": "published_at", "type": "DAY"},
    "pipeline_snapshots": {"field": "snapshot_date", "type": "DAY"},
    "agent_learnings": {"field": "created_at", "type": "DAY"},
    "daily_movement_features": {"field": "trade_date", "type": "DAY"},
    "price_movement_attributions": {"field": "move_date", "type": "DAY"},
    "gold_correlation_daily": {"field": "trade_date", "type": "DAY"},
    "macro_policy_events": {"field": "published_at", "type": "DAY"},
    "movement_event_links": {"field": "move_date", "type": "DAY"},
    "movement_context_daily": {"field": "trade_date", "type": "DAY"},
    "daily_brief_snapshot": {"field": "trade_date", "type": "DAY"},
    "rag_price_facts": {"field": "trade_date", "type": "DAY"},
    "rag_macro_facts": {"field": "release_date", "type": "DAY"},
    "rag_flow_snapshots": {"field": "flow_date", "type": "DAY"},
    "rag_ingestion_log": {"field": "as_of_ts", "type": "DAY"},
}


def create_all_tables(dry_run: bool = False) -> dict:
    """Create all BigQuery tables. Returns {table_name: status}."""
    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery not installed. Run: pip install google-cloud-bigquery")
        return {"error": "google-cloud-bigquery not installed"}

    client = bigquery.Client(project=PROJECT_ID)
    results = {}

    for table_name, schema_fields in TABLE_SCHEMAS.items():
        table_id = f"{FULL_DATASET}.{table_name}"

        if dry_run:
            results[table_name] = "would_create"
            logger.info("[DRY RUN] Would create table: %s", table_id)
            continue

        schema = [
            bigquery.SchemaField(f["name"], f["type"], mode=f["mode"])
            for f in schema_fields
        ]

        table = bigquery.Table(table_id, schema=schema)

        partition = PARTITION_CONFIG.get(table_name)
        if partition:
            if partition["field"] in ("trade_date", "snapshot_date", "move_date"):
                table.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY,
                    field=partition["field"],
                )
            elif partition["field"] in ("published_at", "created_at"):
                table.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY,
                    field=partition["field"],
                )

        try:
            client.get_table(table_id)
            results[table_name] = "already_exists"
            logger.info("Table already exists: %s", table_id)
        except Exception:
            client.create_table(table)
            results[table_name] = "created"
            logger.info("Created table: %s", table_id)

    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    results = create_all_tables(dry_run=dry)
    for t, s in results.items():
        print(f"  {t}: {s}")
