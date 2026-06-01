"""
Dual backend abstraction — DuckDB for local dev, BigQuery for production.

Set MCP_DATA_BACKEND=bigquery for production, defaults to duckdb.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BACKEND = os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()
_DATA_LAKE_DIR = os.environ.get(
    "DATA_LAKE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "data_lake_output"),
)


class DuckDBBackend:
    """Local DuckDB backend — reads directly from Parquet data lake."""

    def __init__(self):
        self._con = None

    @property
    def con(self):
        if self._con is None:
            import duckdb
            self._con = duckdb.connect(":memory:")
            self._register_views()
        return self._con

    def _register_views(self):
        prices_glob = os.path.join(_DATA_LAKE_DIR, "daily_prices", "*.parquet")
        events_glob = os.path.join(_DATA_LAKE_DIR, "events", "*.parquet")

        self.con.execute(f"""
            CREATE OR REPLACE VIEW daily_prices AS
            SELECT * FROM read_parquet('{prices_glob}', union_by_name=true)
        """)

        try:
            self.con.execute(f"""
                CREATE OR REPLACE VIEW events_raw AS
                SELECT * FROM read_parquet('{events_glob}', union_by_name=true)
            """)
        except Exception as e:
            logger.debug("Events parquet not available: %s", e)

    def query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """Execute SQL and return list of dicts."""
        try:
            if params:
                result = self.con.execute(sql, list(params.values()))
            else:
                result = self.con.execute(sql)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]
        except Exception as e:
            logger.warning("[DuckDB] Query failed: %s", e)
            return []

    def insert_rows(self, table: str, rows: List[Dict]) -> int:
        """Insert rows into a DuckDB table (creates if not exists)."""
        if not rows:
            return 0
        import duckdb
        import pandas as pd
        df = pd.DataFrame(rows)
        try:
            self.con.execute(
                f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM df WHERE 1=0"
            )
        except Exception:
            pass
        self.con.execute(f"INSERT INTO {table} SELECT * FROM df")
        return len(rows)

    def execute(self, sql: str) -> None:
        """Run DDL/DML with no result set."""
        self.con.execute(sql)


class BigQueryBackend:
    """Production BigQuery backend."""

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery
            from .bq_schema import PROJECT_ID
            self._client = bigquery.Client(project=PROJECT_ID)
        return self._client

    def _job_config(self, params: Optional[Dict[str, Any]] = None):
        from google.cloud import bigquery
        from .bq_schema import FULL_DATASET

        job_config = bigquery.QueryJobConfig(
            default_dataset=FULL_DATASET,
        )
        if params:
            job_config.query_parameters = [
                bigquery.ScalarQueryParameter(k, "STRING", v)
                for k, v in params.items()
            ]
        return job_config

    def query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """Execute SQL and return list of dicts."""
        try:
            result = self.client.query(sql, job_config=self._job_config(params)).result()
            return [dict(row) for row in result]
        except Exception as e:
            logger.warning("[BigQuery] Query failed: %s", e)
            return []

    def execute(self, sql: str) -> None:
        """Run DDL/DML with no result set; raises on failure."""
        self.client.query(sql, job_config=self._job_config()).result()

    def insert_rows(self, table: str, rows: List[Dict]) -> int:
        """Load rows into BigQuery table via load job (supports historical partitions)."""
        if not rows:
            return 0
        from .bq_schema import FULL_DATASET
        from google.cloud import bigquery

        table_id = f"{FULL_DATASET}.{table}"
        try:
            job_config = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            )
            job = self.client.load_table_from_json(rows, table_id, job_config=job_config)
            job.result()
            if job.errors:
                logger.warning("[BigQuery] Load errors for %s: %s", table, job.errors[:3])
                return 0
            return len(rows)
        except Exception as e:
            logger.warning("[BigQuery] Load failed for %s: %s", table, e)
            return 0


def get_backend():
    """Return the configured backend instance."""
    if BACKEND == "bigquery":
        return BigQueryBackend()
    return DuckDBBackend()


_backend_instance = None


def backend():
    """Singleton backend accessor."""
    global _backend_instance
    if _backend_instance is None:
        _backend_instance = get_backend()
        logger.info("[MCP Data] Using backend: %s", BACKEND)
    return _backend_instance
