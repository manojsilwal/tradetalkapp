"""Temporal helpers — year-filtered graphs and yearly snapshot series."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import get_graph


def get_flows_for_year(
    year: int, root: Optional[str] = None, db_path: Optional[str] = None,
) -> Dict[str, Any]:
    return get_graph(year=year, root=root, db_path=db_path)


def get_flow_series(
    source_id: str,
    target_id: str,
    year_from: int = 2020,
    year_to: int = 2026,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return [{year, amount_est_usd}] for a specific source→target pair."""
    from .db import get_supply_chain_db_path
    import sqlite3

    path = db_path or get_supply_chain_db_path()
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT timestamp_year, amount_est_usd
            FROM supply_chain_edges
            WHERE source_node_id = ? AND target_node_id = ?
              AND timestamp_year BETWEEN ? AND ?
            ORDER BY timestamp_year
            """,
            (source_id, target_id, year_from, year_to),
        ).fetchall()
        return [{"year": r["timestamp_year"], "amount_est_usd": r["amount_est_usd"]} for r in rows]
    finally:
        con.close()


def get_snapshots(
    year_from: int = 2020,
    year_to: int = 2026,
    root: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build per-year graph snapshots."""
    out: List[Dict[str, Any]] = []
    for yr in range(year_from, year_to + 1):
        snap = get_graph(year=yr, root=root, db_path=db_path)
        if snap["edges"]:
            out.append(snap)
    return out
