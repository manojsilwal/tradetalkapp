"""Pydantic models for supply chain API payloads."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SupplyChainNode(BaseModel):
    node_id: str
    name: str
    ticker: Optional[str] = None
    gics_sector: str
    gics_sub_industry: Optional[str] = None
    is_public: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SupplyChainEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    relationship_type: Optional[str] = None
    amount_est_usd: Optional[float] = None
    amount_pct_of_revenue: Optional[float] = None
    timestamp_year: int
    confidence: float = 0.5
    source: str = "seed"
    citation: Optional[str] = None


class GraphResponse(BaseModel):
    year: Optional[int] = None
    root: Optional[str] = None
    nodes: List[SupplyChainNode]
    edges: List[SupplyChainEdge]


class NodeDetailResponse(BaseModel):
    node: SupplyChainNode
    upstream: List[SupplyChainEdge] = Field(default_factory=list)
    downstream: List[SupplyChainEdge] = Field(default_factory=list)


class ExtractPreviewRequest(BaseModel):
    ticker: str
    form: str = "10-K"


class ExtractedEdge(BaseModel):
    source: str
    target: str
    relationship_type: Optional[str] = None
    amount_est_usd: Optional[float] = None
    amount_pct_of_revenue: Optional[float] = None
    year: Optional[int] = None
    confidence: float = 0.5
    citation: Optional[str] = None


class SectorSankeyResponse(BaseModel):
    year: int
    nodes: List[Dict[str, str]]
    links: List[Dict[str, Any]]


class TimelineResponse(BaseModel):
    year_from: int
    year_to: int
    root: Optional[str] = None
    snapshots: List[GraphResponse]
