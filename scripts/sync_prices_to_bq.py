"""
Sync daily_prices parquet files to BigQuery via GCS load job.

Normalizes yfinance OHLCV parquets (Close/Open/Date index) to BQ schema before upload.

Usage:
    PYTHONPATH=. python scripts/sync_prices_to_bq.py --dry-run
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python scripts/sync_prices_to_bq.py
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.mcp_server.bq_schema import FULL_DATASET, GCS_BUCKET, PROJECT_ID

logger = logging.getLogger(__name__)

DATA_LAKE_DIR = os.environ.get(
    "DATA_LAKE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data_lake_output"),
)
PRICES_DIR = os.path.join(DATA_LAKE_DIR, "daily_prices")
GCS_PREFIX = "raw/daily_prices/normalized"

_COL_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Adj Close": "adj_close",
}


def normalize_price_df(df, ticker: str):
    """Map yfinance parquet to BigQuery daily_prices schema (Parquet-safe types)."""
    import pandas as pd

    out = df.copy()
    if out.index.name in (None, "Date", "date") or isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index()
        date_col = out.columns[0]
        if date_col not in ("trade_date", "Date", "date"):
            out = out.rename(columns={date_col: "trade_date"})
        elif date_col in ("Date", "date"):
            out = out.rename(columns={date_col: "trade_date"})

    out = out.rename(columns={k: v for k, v in _COL_MAP.items() if k in out.columns})
    out["symbol"] = ticker.upper()
    # DATE + TIMESTAMP must not be ISO strings — Parquet would store BYTE_ARRAY and BQ load fails.
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    out["ingested_at"] = pd.Timestamp.now(tz="UTC")

    cols = [
        "symbol", "trade_date", "open", "high", "low", "close", "volume",
        "daily_return_pct", "ma_20", "ma_50", "ma_200", "relative_volume", "ingested_at",
    ]
    float_cols = [
        "open", "high", "low", "close", "daily_return_pct",
        "ma_20", "ma_50", "ma_200", "relative_volume",
    ]
    for c in float_cols:
        if c not in out.columns:
            out[c] = pd.NA
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")

    if "volume" not in out.columns:
        out["volume"] = pd.NA
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").round().astype("Int64")

    out["symbol"] = out["symbol"].astype("string")
    return out[cols]


def write_bq_parquet(df, path: str) -> None:
    """Write parquet with explicit types matching BigQuery daily_prices schema."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
    if out["ingested_at"].dt.tz is None:
        out["ingested_at"] = out["ingested_at"].dt.tz_localize("UTC")

    schema = pa.schema([
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("trade_date", pa.date32(), nullable=False),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.int64()),
        pa.field("daily_return_pct", pa.float64()),
        pa.field("ma_20", pa.float64()),
        pa.field("ma_50", pa.float64()),
        pa.field("ma_200", pa.float64()),
        pa.field("relative_volume", pa.float64()),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC")),
    ])
    table = pa.Table.from_pandas(out, schema=schema, preserve_index=False)
    pq.write_table(table, path)


def iter_normalized_frames():
    """Yield normalized DataFrames from local price parquets."""
    import pandas as pd

    paths = glob.glob(os.path.join(PRICES_DIR, "*.parquet"))
    for path in paths:
        ticker = os.path.basename(path).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(path)
            yield normalize_price_df(df, ticker)
        except Exception as e:
            logger.warning("Skip %s: %s", path, e)


def _clear_gcs_normalized_prefix(dry_run: bool = False) -> int:
    """Remove stale normalized parquets (avoids mixed schemas on BQ load)."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    deleted = 0
    for blob in bucket.list_blobs(prefix=f"{GCS_PREFIX}/"):
        if dry_run:
            logger.info("[DRY RUN] Would delete gs://%s/%s", GCS_BUCKET, blob.name)
        else:
            blob.delete()
        deleted += 1
    if deleted:
        logger.info("Cleared %d objects under gs://%s/%s/", deleted, GCS_BUCKET, GCS_PREFIX)
    return deleted


def upload_normalized_to_gcs(dry_run: bool = False) -> int:
    """Normalize local parquets and upload to GCS for BQ load."""
    from google.cloud import storage

    if not os.path.isdir(PRICES_DIR):
        logger.warning("Prices dir not found: %s", PRICES_DIR)
        return 0

    paths = glob.glob(os.path.join(PRICES_DIR, "*.parquet"))
    if not paths:
        logger.warning("No parquet files in %s", PRICES_DIR)
        return 0

    _clear_gcs_normalized_prefix(dry_run=dry_run)

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    count = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for path in paths:
            ticker = os.path.basename(path).replace(".parquet", "").upper()
            import pandas as pd

            try:
                df = normalize_price_df(pd.read_parquet(path), ticker)
            except Exception as e:
                logger.warning("Skip %s: %s", path, e)
                continue

            local_norm = os.path.join(tmpdir, f"{ticker}.parquet")
            write_bq_parquet(df, local_norm)
            blob_name = f"{GCS_PREFIX}/{ticker}.parquet"

            if dry_run:
                logger.info("[DRY RUN] Would upload normalized %s", blob_name)
            else:
                bucket.blob(blob_name).upload_from_filename(local_norm)
                if count and count % 50 == 0:
                    logger.info("Uploaded %d/%d normalized files...", count, len(paths))
            count += 1

    logger.info("Uploaded %d normalized price files to GCS", count)
    return count


def load_prices_from_gcs(dry_run: bool = False, write_disposition: str = "WRITE_TRUNCATE") -> str:
    """BigQuery load job from normalized GCS parquet glob."""
    from google.cloud import bigquery

    uri = f"gs://{GCS_BUCKET}/{GCS_PREFIX}/*.parquet"
    table_id = f"{FULL_DATASET}.daily_prices"

    if dry_run:
        logger.info("[DRY RUN] Would load %s -> %s (%s)", uri, table_id, write_disposition)
        return "dry_run"

    schema = [
        bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("trade_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("open", "FLOAT64"),
        bigquery.SchemaField("high", "FLOAT64"),
        bigquery.SchemaField("low", "FLOAT64"),
        bigquery.SchemaField("close", "FLOAT64"),
        bigquery.SchemaField("volume", "INT64"),
        bigquery.SchemaField("daily_return_pct", "FLOAT64"),
        bigquery.SchemaField("ma_20", "FLOAT64"),
        bigquery.SchemaField("ma_50", "FLOAT64"),
        bigquery.SchemaField("ma_200", "FLOAT64"),
        bigquery.SchemaField("relative_volume", "FLOAT64"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
    ]

    client = bigquery.Client(project=PROJECT_ID)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=getattr(bigquery.WriteDisposition, write_disposition),
        schema=schema,
        time_partitioning=bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="trade_date",
        ),
    )

    logger.info("Starting BQ load job: %s -> %s", uri, table_id)
    job = client.load_table_from_uri(uri, table_id, job_config=job_config)
    job.result()

    table = client.get_table(table_id)
    logger.info("Load complete: %d rows in %s", table.num_rows, table_id)
    return f"loaded_{table.num_rows}_rows"


def normalize_and_load_local(dry_run: bool = False) -> str:
    """Read local parquets, normalize, batch-load via BigQuery JSON load."""
    frames = list(iter_normalized_frames())
    if not frames:
        return "no_files"

    import pandas as pd

    combined = pd.concat(frames, ignore_index=True)
    logger.info("Combined %d rows from %d files", len(combined), len(frames))

    if dry_run:
        return f"dry_run_{len(combined)}_rows"

    from backend.mcp_server.backend import backend

    rows = combined.to_dict(orient="records")
    for row in rows:
        ts = row.get("ingested_at")
        if hasattr(ts, "isoformat"):
            row["ingested_at"] = ts.isoformat()
        td = row.get("trade_date")
        if hasattr(td, "date"):
            row["trade_date"] = td.date().isoformat() if hasattr(td, "date") else str(td)[:10]
        vol = row.get("volume")
        if vol is not None and pd.notna(vol):
            row["volume"] = int(vol)

    batch_size = 5000
    total = 0
    for i in range(0, len(rows), batch_size):
        total += backend().insert_rows("daily_prices", rows[i : i + batch_size])
    return f"inserted_{total}_rows"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Sync daily_prices to BigQuery")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-gcs", action="store_true", help="Skip upload; load from existing GCS files")
    parser.add_argument("--local-insert", action="store_true", help="Insert via load job from local normalize")
    parser.add_argument("--append", action="store_true", help="Append instead of truncate")
    args = parser.parse_args()

    disposition = "WRITE_APPEND" if args.append else "WRITE_TRUNCATE"

    if not args.from_gcs:
        uploaded = upload_normalized_to_gcs(dry_run=args.dry_run)
        logger.info("GCS upload: %d files", uploaded)
        if uploaded == 0 and not args.dry_run:
            print("Result: no_files")
            return

    if args.local_insert:
        result = normalize_and_load_local(dry_run=args.dry_run)
    else:
        result = load_prices_from_gcs(dry_run=args.dry_run, write_disposition=disposition)

    print(f"Result: {result}")


if __name__ == "__main__":
    main()
