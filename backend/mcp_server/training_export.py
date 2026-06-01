"""
Training data export — BQ/DuckDB -> GCS Parquet/JSONL for model fine-tuning.

Supports three export formats:
  - SFT (Supervised Fine-Tuning): instruction/response pairs from agent decisions
  - DPO (Direct Preference Optimization): preferred vs rejected from outcome grading
  - Tabular: features + labels for traditional ML

Usage:
    python -m backend.mcp_server.training_export --format sft --dry-run
    python -m backend.mcp_server.training_export --format tabular --output ./exports/
    python -m backend.mcp_server.training_export --format all --gcs
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


SFT_EXPORT_SQL = """
SELECT
    ps.snapshot_type,
    ps.snapshot_date,
    ps.summary_text AS context,
    al.observation AS agent_response,
    al.agent_id,
    al.learning_type,
    al.market_regime
FROM {table_prefix}pipeline_snapshots ps
JOIN {table_prefix}agent_learnings al
    ON ps.snapshot_date = CAST(al.created_at AS DATE)
WHERE al.observation IS NOT NULL
  AND LENGTH(al.observation) > 50
ORDER BY ps.snapshot_date DESC
"""

TABULAR_EXPORT_SQL = """
SELECT
    m.symbol,
    m.trade_date,
    m.daily_return_pct,
    m.return_zscore_60d,
    m.relative_volume,
    m.market_regime,
    m.catalyst_status,
    m.primary_cause_category,
    m.primary_cause_headline,
    m.primary_cause_weight,
    m.linked_events_json,
    m.spx_return,
    m.risk_regime,
    LEAD(m.daily_return_pct) OVER (
        PARTITION BY m.symbol ORDER BY m.trade_date
    ) AS label_1d
FROM {table_prefix}movement_context_daily m
WHERE m.daily_return_pct IS NOT NULL
ORDER BY m.trade_date, m.symbol
"""

DPO_EXPORT_SQL = """
SELECT
    al1.observation AS chosen_response,
    al2.observation AS rejected_response,
    ps.summary_text AS context,
    al1.market_regime
FROM {table_prefix}agent_learnings al1
JOIN {table_prefix}agent_learnings al2
    ON al1.agent_id = al2.agent_id
    AND CAST(al1.created_at AS DATE) = CAST(al2.created_at AS DATE)
    AND al1.learning_id != al2.learning_id
JOIN {table_prefix}pipeline_snapshots ps
    ON CAST(al1.created_at AS DATE) = ps.snapshot_date
WHERE al1.learning_type = 'reflection'
  AND al2.learning_type = 'note'
  AND LENGTH(al1.observation) > 100
  AND LENGTH(al2.observation) > 50
LIMIT 10000
"""


def _get_table_prefix() -> str:
    """Return table prefix based on backend."""
    backend_type = os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()
    if backend_type == "bigquery":
        from .bq_schema import FULL_DATASET
        return f"{FULL_DATASET}."
    return ""


def export_sft(output_dir: str, dry_run: bool = False) -> str:
    """Export SFT training pairs as JSONL."""
    prefix = _get_table_prefix()
    sql = SFT_EXPORT_SQL.format(table_prefix=prefix)

    if dry_run:
        logger.info("[Export SFT] Would run query and write to %s", output_dir)
        return "dry_run"

    from .backend import backend
    rows = backend().query(sql)

    if not rows:
        logger.info("[Export SFT] No rows returned")
        return "empty"

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"sft_{datetime.now().strftime('%Y%m%d')}.jsonl")

    with open(output_path, "w") as f:
        for row in rows:
            instruction = (
                f"You are a {row.get('agent_id', 'finance')} agent. "
                f"Market regime: {row.get('market_regime', 'unknown')}. "
                f"Context: {row.get('context', '')}"
            )
            record = {
                "instruction": instruction,
                "response": row.get("agent_response", ""),
                "metadata": {
                    "agent_id": row.get("agent_id"),
                    "learning_type": row.get("learning_type"),
                    "date": str(row.get("snapshot_date", "")),
                },
            }
            f.write(json.dumps(record) + "\n")

    logger.info("[Export SFT] Wrote %d records to %s", len(rows), output_path)
    return output_path


def export_tabular(output_dir: str, dry_run: bool = False) -> str:
    """Export tabular features + labels as Parquet."""
    prefix = _get_table_prefix()
    sql = TABULAR_EXPORT_SQL.format(table_prefix=prefix)

    if dry_run:
        logger.info("[Export Tabular] Would run query and write to %s", output_dir)
        return "dry_run"

    from .backend import backend
    rows = backend().query(sql)

    if not rows:
        logger.info("[Export Tabular] No rows returned")
        return "empty"

    import pandas as pd
    df = pd.DataFrame(rows)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"tabular_{datetime.now().strftime('%Y%m%d')}.parquet")
    df.to_parquet(output_path, index=False)

    logger.info("[Export Tabular] Wrote %d rows to %s", len(df), output_path)
    return output_path


def export_dpo(output_dir: str, dry_run: bool = False) -> str:
    """Export DPO preference pairs as JSONL."""
    prefix = _get_table_prefix()
    sql = DPO_EXPORT_SQL.format(table_prefix=prefix)

    if dry_run:
        logger.info("[Export DPO] Would run query and write to %s", output_dir)
        return "dry_run"

    from .backend import backend
    rows = backend().query(sql)

    if not rows:
        logger.info("[Export DPO] No rows returned (need graded outcomes first)")
        return "empty"

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"dpo_{datetime.now().strftime('%Y%m%d')}.jsonl")

    with open(output_path, "w") as f:
        for row in rows:
            record = {
                "context": row.get("context", ""),
                "chosen": row.get("chosen_response", ""),
                "rejected": row.get("rejected_response", ""),
                "metadata": {"market_regime": row.get("market_regime", "")},
            }
            f.write(json.dumps(record) + "\n")

    logger.info("[Export DPO] Wrote %d records to %s", len(rows), output_path)
    return output_path


def upload_to_gcs(local_path: str, gcs_prefix: str = "exports/training") -> str:
    """Upload a local export file to GCS."""
    from google.cloud import storage
    from .bq_schema import GCS_BUCKET

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob_name = f"{gcs_prefix}/{os.path.basename(local_path)}"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    gcs_path = f"gs://{GCS_BUCKET}/{blob_name}"
    logger.info("[GCS Upload] %s -> %s", local_path, gcs_path)
    return gcs_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Export training datasets")
    parser.add_argument("--format", choices=["sft", "dpo", "tabular", "all"], default="all")
    parser.add_argument("--output", default="./training_exports")
    parser.add_argument("--gcs", action="store_true", help="Upload to GCS after export")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = {}
    formats = ["sft", "dpo", "tabular"] if args.format == "all" else [args.format]

    for fmt in formats:
        if fmt == "sft":
            results["sft"] = export_sft(args.output, dry_run=args.dry_run)
        elif fmt == "dpo":
            results["dpo"] = export_dpo(args.output, dry_run=args.dry_run)
        elif fmt == "tabular":
            results["tabular"] = export_tabular(args.output, dry_run=args.dry_run)

    for fmt, path in results.items():
        print(f"  {fmt}: {path}")
        if args.gcs and path not in ("dry_run", "empty") and os.path.isfile(path):
            gcs_path = upload_to_gcs(path)
            print(f"    -> {gcs_path}")
