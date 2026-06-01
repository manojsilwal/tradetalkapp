"""
Upload existing Parquet data lake to GCS raw zone.

Usage:
    python scripts/initial_load_to_gcs.py --dry-run
    python scripts/initial_load_to_gcs.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.mcp_server.bq_schema import GCS_BUCKET

logger = logging.getLogger(__name__)

DATA_LAKE_DIR = os.environ.get(
    "DATA_LAKE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data_lake_output"),
)

GCS_PREFIX_MAP = {
    "daily_prices": "raw/daily_prices",
    "quarterly_financials": "raw/quarterly_financials",
    "events": "raw/events",
    "macro_history": "raw/macro_history",
    "rag_summaries": "raw/rag_summaries",
}


def upload_directory(local_dir: str, gcs_prefix: str, bucket_name: str, dry_run: bool = False) -> int:
    """Upload all .parquet and .json files from local_dir to GCS."""
    from google.cloud import storage

    if not os.path.isdir(local_dir):
        logger.warning("Directory not found: %s", local_dir)
        return 0

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    count = 0

    for root, _dirs, files in os.walk(local_dir):
        for fname in files:
            if not (fname.endswith(".parquet") or fname.endswith(".json")):
                continue

            local_path = os.path.join(root, fname)
            rel_path = os.path.relpath(local_path, local_dir)
            blob_name = f"{gcs_prefix}/{rel_path}"

            if dry_run:
                logger.info("[DRY RUN] Would upload: %s -> gs://%s/%s", local_path, bucket_name, blob_name)
            else:
                blob = bucket.blob(blob_name)
                if blob.exists():
                    logger.debug("Already exists: gs://%s/%s", bucket_name, blob_name)
                else:
                    blob.upload_from_filename(local_path)
                    logger.info("Uploaded: %s -> gs://%s/%s", local_path, bucket_name, blob_name)
            count += 1

    return count


def main():
    parser = argparse.ArgumentParser(description="Upload data lake to GCS")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be uploaded")
    parser.add_argument("--bucket", default=GCS_BUCKET, help="GCS bucket name")
    parser.add_argument("--data-dir", default=DATA_LAKE_DIR, help="Local data lake directory")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    total = 0
    for subdir, gcs_prefix in GCS_PREFIX_MAP.items():
        local_path = os.path.join(args.data_dir, subdir)
        logger.info("Processing: %s -> gs://%s/%s/", local_path, args.bucket, gcs_prefix)
        count = upload_directory(local_path, gcs_prefix, args.bucket, dry_run=args.dry_run)
        total += count
        logger.info("  %d files %s", count, "would be uploaded" if args.dry_run else "processed")

    logger.info("Total: %d files", total)

    if not args.dry_run:
        logger.info("\nNext: Create BigQuery external tables:")
        logger.info("  python -m backend.mcp_server.bq_schema")


if __name__ == "__main__":
    main()
