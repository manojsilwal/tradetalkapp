"""Idempotent seed: load backend/data/supply_chains.json into supply_chain.db."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from .db import get_supply_chain_db_path, init_supply_chain_db

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "supply_chains.json"


def _load_json() -> dict:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def seed_supply_chain_db(db_path: Optional[str] = None) -> None:
    path = db_path or get_supply_chain_db_path()
    init_supply_chain_db()
    data = _load_json()

    con = sqlite3.connect(path, check_same_thread=False)
    try:
        cur = con.cursor()
        for n in data.get("nodes", []):
            cur.execute(
                """
                INSERT INTO supply_chain_nodes
                    (node_id, name, ticker, gics_sector, gics_sub_industry, is_public, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    name=excluded.name, ticker=excluded.ticker,
                    gics_sector=excluded.gics_sector, gics_sub_industry=excluded.gics_sub_industry,
                    is_public=excluded.is_public, metadata_json=excluded.metadata_json
                """,
                (
                    n["node_id"],
                    n["name"],
                    n.get("ticker"),
                    n["gics_sector"],
                    n.get("gics_sub_industry"),
                    1 if n.get("is_public", True) else 0,
                    json.dumps(n.get("metadata", {})),
                ),
            )

        for e in data.get("edges", []):
            years = e.get("years", {})
            for year_str, amount in years.items():
                year = int(year_str)
                edge_id = f"{e['source']}__{e['target']}__{e.get('relationship_type', 'unknown')}__{year}"
                cur.execute(
                    """
                    INSERT INTO supply_chain_edges
                        (edge_id, source_node_id, target_node_id, relationship_type,
                         amount_est_usd, amount_pct_of_revenue, timestamp_year,
                         confidence, source, citation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seed', ?)
                    ON CONFLICT(edge_id) DO UPDATE SET
                        amount_est_usd=excluded.amount_est_usd,
                        confidence=excluded.confidence,
                        citation=excluded.citation
                    """,
                    (
                        edge_id,
                        e["source"],
                        e["target"],
                        e.get("relationship_type"),
                        amount,
                        e.get("amount_pct_of_revenue"),
                        year,
                        e.get("confidence", 0.5),
                        e.get("citation"),
                    ),
                )

        con.commit()
        logger.info("[supply_chain] seed complete for %s", path)
    finally:
        con.close()


def node_count(db_path: Optional[str] = None) -> int:
    path = db_path or get_supply_chain_db_path()
    con = sqlite3.connect(path, check_same_thread=False)
    try:
        return con.execute("SELECT COUNT(*) FROM supply_chain_nodes").fetchone()[0]
    except Exception:
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_supply_chain_db()
