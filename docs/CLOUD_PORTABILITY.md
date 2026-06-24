# Cloud Portability — Swap GCP ↔ AWS ↔ Azure without losing work

Companion to [`docs/finance-brain-architecture.html`](./finance-brain-architecture.html) (Section 10).

**Goal:** make the cloud a swappable dependency, the same way the LLM router ([`backend/llm_client.py`](../backend/llm_client.py)) and the data-provider router make models and data sources swappable. A cloud move should be a **config + data-sync exercise, not a rewrite**.

**Principle:** every cloud call goes through a *port* (a thin interface). Cloud-specific code lives only in *adapters* selected by an env flag — exactly like the existing `VECTOR_BACKEND` / `MCP_DATA_BACKEND` switches.

---

## 1. Lock-in avoidance rules (do this from day one)

1. **DuckDB + Parquet are the source of truth.** Treat BigQuery / Athena / Synapse as optional accelerators, never the canonical store.
2. **Supabase is the cloud-neutral data spine** (Postgres + pgvector + cache). It is not tied to GCP, so a cloud move barely touches the data layer.
3. **One Dockerfile.** The app ships as a single container image that any container runtime can host.
4. **Terraform over `gcloud`-only scripts.** Infra is a provider-swappable module, not bash glued to one cloud.
5. **Config via env.** No SDK or bucket name is hardcoded in app logic; everything resolves through a port + env var.
6. **Secrets via a `SecretsPort`** that populates env vars from whichever cloud secret store is active.

---

## 2. Service equivalents

| Concern | GCP | AWS | Azure | Portability key |
|--------|-----|-----|-------|-----------------|
| Container compute | Cloud Run | App Runner / ECS Fargate | Container Apps | same Docker image |
| Object storage | GCS | S3 | Blob Storage | S3-compatible API + `StoragePort` |
| Relational DB | Supabase / Cloud SQL | Supabase / RDS | Supabase / Azure DB | Postgres wire protocol |
| Cache | Memorystore | ElastiCache | Azure Cache for Redis | Redis protocol (or Supabase) |
| Warehouse | BigQuery | Athena / Redshift | Synapse / Fabric | **DuckDB + Parquet core; warehouse optional** |
| Always-free VM (backfill loader) | e2-micro | EC2 t4g.nano | B1s | plain Linux + Python |
| Scheduler / cron | Cloud Scheduler | EventBridge | Logic Apps | GitHub Actions cron (already neutral) + APScheduler |
| Secrets | Secret Manager | Secrets Manager | Key Vault | env injection via `SecretsPort` |
| Vector store | Supabase pgvector | same | same | pgvector / Chroma both portable |

---

## 3. Port interfaces (Python stubs)

Put these under `backend/ports/`. Adapters under `backend/ports/adapters/<cloud>/`. Select with env (see §5).

```python
# backend/ports/base.py
from __future__ import annotations
from typing import Protocol, Iterable, Optional, Any, runtime_checkable


@runtime_checkable
class StoragePort(Protocol):
    """Object storage: GCS | S3 | Azure Blob."""
    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> Iterable[str]: ...
    def exists(self, key: str) -> bool: ...
    def url(self, key: str, *, expires_s: int = 3600) -> str: ...


@runtime_checkable
class CachePort(Protocol):
    """Hot/warm cache: Redis (Memorystore/ElastiCache/Azure) | Supabase."""
    def get(self, key: str) -> Optional[bytes]: ...
    def set(self, key: str, value: bytes, *, ttl_s: int | None = None) -> None: ...
    def delete(self, key: str) -> None: ...


@runtime_checkable
class RelationalPort(Protocol):
    """Postgres anywhere (Supabase / Cloud SQL / RDS / Azure DB)."""
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> int: ...
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]: ...


@runtime_checkable
class WarehousePort(Protocol):
    """Analytics SQL: DuckDB (default, portable) | BigQuery | Athena | Synapse."""
    def sql(self, query: str) -> list[dict]: ...


@runtime_checkable
class SecretsPort(Protocol):
    """Resolve secrets from env | GSM | ASM | Key Vault."""
    def resolve(self, key: str) -> Optional[str]: ...


@runtime_checkable
class SchedulerPort(Protocol):
    """Register a cron job that POSTs to an HTTP endpoint with a shared secret.
    Implemented by GitHub Actions / Cloud Scheduler / EventBridge / Logic Apps."""
    def ensure_job(self, name: str, cron: str, target_url: str) -> None: ...
```

Example adapter (storage) and factory:

```python
# backend/ports/adapters/aws/storage.py
import boto3
from backend.ports.base import StoragePort

class S3Storage:  # structurally satisfies StoragePort
    def __init__(self, bucket: str):
        self._s3 = boto3.client("s3")
        self._bucket = bucket
    def put(self, key, data, *, content_type=None):
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data,
                            **({"ContentType": content_type} if content_type else {}))
        return f"s3://{self._bucket}/{key}"
    def get(self, key):
        return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
    def list(self, prefix):
        p = self._s3.get_paginator("list_objects_v2")
        for page in p.paginate(Bucket=self._bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                yield o["Key"]
    def exists(self, key):
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key); return True
        except self._s3.exceptions.ClientError:
            return False
    def url(self, key, *, expires_s=3600):
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_s)
```

```python
# backend/ports/factory.py
import os
from backend.ports.base import StoragePort

def get_storage() -> StoragePort:
    backend = os.getenv("STORAGE_BACKEND", os.getenv("CLOUD_PROVIDER", "gcp"))
    if backend in ("gcp", "gcs"):
        from backend.ports.adapters.gcp.storage import GcsStorage
        return GcsStorage(os.environ["GCS_BUCKET"])
    if backend in ("aws", "s3"):
        from backend.ports.adapters.aws.storage import S3Storage
        return S3Storage(os.environ["S3_BUCKET"])
    if backend in ("azure", "blob"):
        from backend.ports.adapters.azure.storage import BlobStorage
        return BlobStorage(os.environ["AZURE_BLOB_CONTAINER"])
    raise ValueError(f"unknown STORAGE_BACKEND={backend!r}")
```

App code only ever calls `get_storage().put(...)` — never an SDK directly.

---

## 4. Terraform layout (multi-cloud)

```text
infra/
  modules/
    app/                  # cloud-neutral wiring (vars in, outputs out)
  providers/
    gcp/
      main.tf             # Cloud Run + GCS + (Supabase or Cloud SQL) + Memorystore + e2-micro
      variables.tf
    aws/
      main.tf             # App Runner/Fargate + S3 + RDS + ElastiCache + EC2
      variables.tf
    azure/
      main.tf             # Container Apps + Blob + Azure DB + Redis + B-series VM
      variables.tf
  envs/
    prod.tfvars
    staging.tfvars
```

Switch clouds by pointing at a different `providers/<cloud>` while keeping the same `modules/app` resource graph (container + bucket + db + cache + scheduler + VM).

---

## 5. Environment flags

```bash
# Cloud selection
CLOUD_PROVIDER=gcp            # gcp | aws | azure  (default for all ports)

# Per-port overrides (optional; fall back to CLOUD_PROVIDER)
STORAGE_BACKEND=gcp          # gcp | aws | azure
CACHE_BACKEND=supabase       # redis | supabase
WAREHOUSE_BACKEND=duckdb     # duckdb | bigquery | athena | synapse
SECRETS_BACKEND=env          # env | gsm | asm | keyvault

# Connection details (only those for the active cloud are required)
GCS_BUCKET=...
S3_BUCKET=...
AZURE_BLOB_CONTAINER=...
DATABASE_URL=postgresql://...        # Supabase/RDS/Cloud SQL/Azure DB
REDIS_URL=redis://...                # any managed Redis (optional)
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```

Keep this list mirrored in `backend/.env.example`.

---

## 6. Migration playbook (zero data loss)

1. **Confirm all state is portable** — Parquet in object storage, Postgres rows, model artifacts as files, vector embeddings. Nothing critical trapped in a proprietary service.
2. **Move the data:**
   - Object storage: `gsutil -m rsync` → `aws s3 sync` / `azcopy copy`.
   - Postgres: keep **Supabase** (no move needed) **or** `pg_dump | pg_restore`.
   - Vectors: Supabase pgvector travels with Postgres; Chroma is a directory copy.
3. **Provision infra** with the target `providers/<cloud>` Terraform module.
4. **Deploy the same container** to the new runtime; set `CLOUD_PROVIDER` + connection envs.
5. **Parity check** — run E2E + shadow-mode against the new stack (see Section 06 UI parity).
6. **Cut over** DNS/traffic. **Rollback** = point back to the old stack.
7. **No retrain, no re-ingest** — model artifacts + feature store + decision ledger carry over as files/rows; the brain wakes up identical.

---

## 7. What is already portable today (no work needed)

- Single FastAPI app in a container (`docker-compose.gcp.yml` proves the image runs standalone).
- GitHub Actions cron hitting secured HTTP endpoints — cloud-neutral already.
- DuckDB-over-Parquet analytics path (`backend/mcp_server/backend.py`) — BigQuery already optional behind `MCP_DATA_BACKEND`.
- Supabase Postgres + pgvector — runs the same regardless of cloud.
- APScheduler in-process jobs — no cloud dependency.

## 8. What needs a port/adapter wrapper (the ~10% of work)

- GCS upload/download helpers (`scripts/*gcs*.py`, data-lake sync) → `StoragePort`.
- `backend/gcp_settings.py` and Cloud SQL connector specifics → config + `RelationalPort`.
- `scripts/deploy_api_cloudrun.sh`, `cloudbuild.api.yaml` → Terraform `providers/gcp` + a generic deploy step.
- Any direct Memorystore client → `CachePort` (or stay on Supabase cache).
