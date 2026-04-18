"""
Read-only HTTP surface for the RSPL resource registry.

These endpoints exist so operators and post-hoc analysts can inspect which
prompt version shipped to users at any moment. They intentionally do NOT
expose ``update`` / ``restore`` in Phase A — those must go through the
code path + human review until Phase B (SEPL) invariants are in place.

Mount path: ``/resources/*``. No auth scope beyond the global CORS policy —
all returned data is already in the repo or auditable by design.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..deps import resource_registry
from ..resource_registry import ResourceKind

router = APIRouter(prefix="/resources", tags=["resources"])


# ── Response models ──────────────────────────────────────────────────────────


class ResourceListEntry(BaseModel):
    name: str
    kind: str
    version: str
    description: str
    learnable: bool
    source_path: str
    created_at: float


class ResourceDetail(ResourceListEntry):
    # `schema` collides with BaseModel's method name in pydantic v1/v2, so we
    # use a distinct attribute name and serialize under the public key "schema".
    model_config = ConfigDict(populate_by_name=True)

    body: str
    metadata: dict
    resource_schema: Optional[dict] = Field(
        default=None, alias="schema", serialization_alias="schema"
    )
    fallback: Optional[object] = None


class LineageEntry(BaseModel):
    id: int
    name: str
    kind: str
    from_version: Optional[str]
    to_version: str
    operation: str
    reason: str
    actor: str
    created_at: float


class RegistrySummary(BaseModel):
    count: int
    db_path: str
    generated_at: float
    snapshot_id: str


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=RegistrySummary)
def get_summary():
    """High-level health: count, snapshot hash, db location."""
    snap = resource_registry.snapshot()
    return RegistrySummary(
        count=snap["count"],
        db_path=snap["db_path"],
        generated_at=snap["generated_at"],
        snapshot_id=resource_registry.snapshot_id(),
    )


@router.get("/", response_model=List[ResourceListEntry])
def list_resources(kind: Optional[str] = Query(default=None, description="prompt|agent|tool|env|mem")):
    """List all active resources. Optional ``?kind=prompt`` filter."""
    kind_enum: Optional[ResourceKind] = None
    if kind is not None:
        try:
            kind_enum = ResourceKind(kind)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"unknown kind={kind!r}; expected one of "
                f"{[k.value for k in ResourceKind]}",
            ) from e

    records = resource_registry.list(kind_enum)
    return [
        ResourceListEntry(
            name=r.name,
            kind=r.kind.value,
            version=r.version,
            description=r.description,
            learnable=r.learnable,
            source_path=r.source_path,
            created_at=r.created_at,
        )
        for r in records
    ]


@router.get(
    "/{name}",
    response_model=ResourceDetail,
    response_model_by_alias=True,
)
def get_resource(
    name: str,
    version: str = Query(default="latest", description="Semver string or 'latest'"),
):
    """Return the full body + metadata + schema/fallback for a resource."""
    rec = resource_registry.get(name, version)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"{name}@{version} not found")
    return ResourceDetail(
        name=rec.name,
        kind=rec.kind.value,
        version=rec.version,
        description=rec.description,
        learnable=rec.learnable,
        source_path=rec.source_path,
        created_at=rec.created_at,
        body=rec.body,
        metadata=rec.metadata,
        resource_schema=rec.schema,
        fallback=rec.fallback,
    )


@router.get("/{name}/versions", response_model=List[str])
def get_versions(name: str):
    """All versions of ``name``, newest semver first."""
    versions = resource_registry.versions(name)
    if not versions:
        raise HTTPException(status_code=404, detail=f"{name!r} has no versions")
    return versions


@router.get("/{name}/lineage", response_model=List[LineageEntry])
def get_lineage(
    name: str,
    limit: int = Query(default=50, ge=1, le=500),
):
    """Audit trail for this resource (register/update/restore entries)."""
    events = resource_registry.lineage(name, limit=limit)
    if not events:
        raise HTTPException(status_code=404, detail=f"{name!r} has no lineage entries")
    return [LineageEntry(**e) for e in events]
