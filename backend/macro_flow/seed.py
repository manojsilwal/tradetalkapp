"""
Idempotent SQLite seed: categories, entities, entity_category_map, graph_edges.
Run: PYTHONPATH=. python -m backend.macro_flow.seed
"""
from __future__ import annotations

import logging
import sqlite3
import time

from .db import get_macro_flow_db_path, init_macro_flow_db
from .graph.value_chains import edges_for_sqlite
from .taxonomy.seed_taxonomy import CATEGORIES, TAXONOMY, validate_taxonomy

logger = logging.getLogger(__name__)


def seed_macro_flow_db(db_path: str | None = None) -> None:
    path = db_path or get_macro_flow_db_path()
    init_macro_flow_db()
    validate_taxonomy()
    now = time.time()

    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        for cid, name, color, desc in CATEGORIES:
            cur.execute(
                """
                INSERT INTO macro_categories (category_id, name, parent_id, level, chain_position, color_hex, description, created_at)
                VALUES (?, ?, NULL, 1, NULL, ?, ?, ?)
                ON CONFLICT(category_id) DO UPDATE SET
                    name=excluded.name, color_hex=excluded.color_hex, description=excluded.description
                """,
                (cid, name, color, desc, now),
            )

        for cid, tickers in TAXONOMY.items():
            for ticker, weight in tickers:
                cur.execute(
                    """
                    INSERT INTO macro_entities (entity_id, ticker, name, asset_type, market_cap, is_active, updated_at)
                    VALUES (?, ?, ?, 'stock', NULL, 1, ?)
                    ON CONFLICT(entity_id) DO UPDATE SET ticker=excluded.ticker, name=excluded.name, updated_at=excluded.updated_at
                    """,
                    (ticker, ticker, ticker, now),
                )
                cur.execute(
                    """
                    INSERT INTO entity_category_map (entity_id, category_id, weight, is_primary, added_by)
                    VALUES (?, ?, ?, 1, 'seed')
                    ON CONFLICT(entity_id, category_id) DO UPDATE SET weight=excluded.weight
                    """,
                    (ticker, cid, weight),
                )

        for eid, src, tgt, rel, lag, strength, desc in edges_for_sqlite():
            cur.execute(
                """
                INSERT INTO graph_edges (edge_id, source_category, target_category, relationship_type, lag_days, base_strength, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edge_id) DO UPDATE SET
                    source_category=excluded.source_category,
                    target_category=excluded.target_category,
                    relationship_type=excluded.relationship_type,
                    lag_days=excluded.lag_days,
                    base_strength=excluded.base_strength,
                    description=excluded.description
                """,
                (eid, src, tgt, rel, lag, strength, desc),
            )

        con.commit()
        logger.info("[macro_flow] seed complete for %s", path)
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_macro_flow_db()
