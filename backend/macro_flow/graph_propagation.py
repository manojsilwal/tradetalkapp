"""Propagate category flow along graph_edges for Sankey / value-chain."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence


def compute_edge_flow_rows(
    edges: Sequence[Dict[str, Any]],
    category_flow_score: Dict[str, float],
) -> List[Dict[str, Any]]:
    """
    Simple propagation: magnitude = base_strength * source_flow_score.
    direction is sign of magnitude.
    """
    rows: List[Dict[str, Any]] = []
    for e in edges:
        eid = e.get("edge_id") or ""
        src = e.get("source_category") or ""
        base = float(e.get("base_strength") or 0.5)
        fs = float(category_flow_score.get(src, 0.0))
        mag = base * fs
        rows.append(
            {
                "edge_id": eid,
                "source_category": src,
                "target_category": e.get("target_category") or "",
                "flow_magnitude": float(mag),
                "direction": 1 if mag >= 0 else -1,
                "confidence": min(1.0, 0.4 + abs(fs) * 0.5),
            }
        )
    return rows


def build_nx_graph(edges: Sequence[Dict[str, Any]]):
    """Optional NetworkX DiGraph for downstream analytics."""
    import networkx as nx

    g = nx.DiGraph()
    for e in edges:
        s, t = e.get("source_category"), e.get("target_category")
        if s and t:
            g.add_edge(s, t, edge_id=e.get("edge_id"), base_strength=e.get("base_strength"))
    return g
