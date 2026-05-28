"""Aggregate company-level edges into sector-to-sector Sankey payloads."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from .store import get_graph


def sector_sankey(
    year: int, db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Roll up company edges for a single year into GICS sector flows."""
    graph = get_graph(year=year, db_path=db_path)
    node_sector = {n["node_id"]: n["gics_sector"] for n in graph["nodes"]}

    agg: Dict[tuple, float] = defaultdict(float)
    for e in graph["edges"]:
        src_sec = node_sector.get(e["source_node_id"])
        tgt_sec = node_sector.get(e["target_node_id"])
        if not src_sec or not tgt_sec or src_sec == tgt_sec:
            continue
        amount = e.get("amount_est_usd") or 0
        agg[(src_sec, tgt_sec)] += amount

    sector_set = set()
    links: List[Dict[str, Any]] = []
    for (src, tgt), val in sorted(agg.items()):
        sector_set.add(src)
        sector_set.add(tgt)
        links.append({"source": src, "target": tgt, "value": val})

    nodes = [{"id": s, "name": s} for s in sorted(sector_set)]
    return {"year": year, "nodes": nodes, "links": links}


def sector_sankey_timeline(
    year_from: int = 2020,
    year_to: int = 2026,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for yr in range(year_from, year_to + 1):
        snap = sector_sankey(yr, db_path=db_path)
        if snap["links"]:
            out.append(snap)
    return out
