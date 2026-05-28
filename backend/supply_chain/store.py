"""SQLite read helpers for supply_chain.db — graph BFS, node detail, edge queries."""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from typing import Any, Dict, List, Optional, Set

from .db import get_supply_chain_db_path

_ROW_TO_NODE_FIELDS = (
    "node_id", "name", "ticker", "gics_sector", "gics_sub_industry", "is_public", "metadata_json",
)
_ROW_TO_EDGE_FIELDS = (
    "edge_id", "source_node_id", "target_node_id", "relationship_type",
    "amount_est_usd", "amount_pct_of_revenue", "timestamp_year",
    "confidence", "source", "citation",
)


def _conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    p = db_path or get_supply_chain_db_path()
    c = sqlite3.connect(p, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _row_to_node(r: sqlite3.Row) -> Dict[str, Any]:
    d = {k: r[k] for k in _ROW_TO_NODE_FIELDS}
    d["is_public"] = bool(d["is_public"])
    d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
    return d


def _row_to_edge(r: sqlite3.Row) -> Dict[str, Any]:
    return {k: r[k] for k in _ROW_TO_EDGE_FIELDS}


# ── Graph BFS ────────────────────────────────────────────────────────────────

def get_graph(
    year: Optional[int] = None,
    root: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return nodes + edges, optionally filtered by year and/or BFS from root."""
    con = _conn(db_path)
    try:
        if year:
            edges_rows = con.execute(
                "SELECT * FROM supply_chain_edges WHERE timestamp_year = ?", (year,)
            ).fetchall()
        else:
            edges_rows = con.execute("SELECT * FROM supply_chain_edges").fetchall()

        edges = [_row_to_edge(r) for r in edges_rows]

        if root:
            reachable = _bfs_reachable(root, edges)
            edges = [e for e in edges if e["source_node_id"] in reachable or e["target_node_id"] in reachable]
            node_ids = reachable
        else:
            node_ids = set()
            for e in edges:
                node_ids.add(e["source_node_id"])
                node_ids.add(e["target_node_id"])

        if not node_ids:
            all_nodes = con.execute("SELECT * FROM supply_chain_nodes").fetchall()
            nodes = [_row_to_node(r) for r in all_nodes]
        else:
            placeholders = ",".join("?" for _ in node_ids)
            nodes_rows = con.execute(
                f"SELECT * FROM supply_chain_nodes WHERE node_id IN ({placeholders})",
                list(node_ids),
            ).fetchall()
            nodes = [_row_to_node(r) for r in nodes_rows]

        return {"year": year, "root": root, "nodes": nodes, "edges": edges}
    finally:
        con.close()


def _bfs_reachable(root: str, edges: List[Dict[str, Any]]) -> Set[str]:
    """BFS in both directions (upstream + downstream) from root."""
    adj: Dict[str, Set[str]] = {}
    for e in edges:
        adj.setdefault(e["source_node_id"], set()).add(e["target_node_id"])
        adj.setdefault(e["target_node_id"], set()).add(e["source_node_id"])

    visited: Set[str] = set()
    queue: deque[str] = deque([root])
    while queue:
        n = queue.popleft()
        if n in visited:
            continue
        visited.add(n)
        for neighbor in adj.get(n, set()):
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


# ── Node detail ──────────────────────────────────────────────────────────────

def get_node_detail(
    node_id: str, year: Optional[int] = None, db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    con = _conn(db_path)
    try:
        row = con.execute("SELECT * FROM supply_chain_nodes WHERE node_id = ?", (node_id,)).fetchone()
        if not row:
            return None
        node = _row_to_node(row)

        base_q = "SELECT * FROM supply_chain_edges WHERE {} = ?"
        params_up: list = [node_id]
        params_down: list = [node_id]
        q_up = base_q.format("target_node_id")
        q_down = base_q.format("source_node_id")
        if year:
            q_up += " AND timestamp_year = ?"
            q_down += " AND timestamp_year = ?"
            params_up.append(year)
            params_down.append(year)

        upstream = [_row_to_edge(r) for r in con.execute(q_up, params_up).fetchall()]
        downstream = [_row_to_edge(r) for r in con.execute(q_down, params_down).fetchall()]
        return {"node": node, "upstream": upstream, "downstream": downstream}
    finally:
        con.close()


# ── All nodes (for seed-empty check) ─────────────────────────────────────────

def list_all_nodes(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    con = _conn(db_path)
    try:
        rows = con.execute("SELECT * FROM supply_chain_nodes ORDER BY node_id").fetchall()
        return [_row_to_node(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()
