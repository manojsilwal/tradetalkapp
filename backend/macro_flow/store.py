"""SQLite read/write helpers for macro_flow.db."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Sequence, Tuple

from .db import get_macro_flow_db_path
from .taxonomy.seed_taxonomy import CATEGORIES, TAXONOMY

logger = logging.getLogger(__name__)


def _conn(db_path: str | None = None) -> sqlite3.Connection:
    p = db_path or get_macro_flow_db_path()
    c = sqlite3.connect(p, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def list_categories_from_db(db_path: str | None = None) -> List[Dict[str, Any]]:
    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT category_id, name, color_hex, description FROM macro_categories ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            cid = r["category_id"]
            n = con.execute(
                "SELECT COUNT(*) FROM entity_category_map WHERE category_id=?",
                (cid,),
            ).fetchone()[0]
            out.append(
                {
                    "category_id": cid,
                    "name": r["name"],
                    "color_hex": r["color_hex"],
                    "description": r["description"] or "",
                    "entity_count": int(n),
                }
            )
        return out
    finally:
        con.close()


def load_category_weights(db_path: str | None = None) -> Dict[str, List[Tuple[str, float]]]:
    """category_id -> [(ticker, weight), ...] from DB; empty dict if unseeded."""
    con = _conn(db_path)
    try:
        rows = con.execute(
            """
            SELECT m.category_id, m.entity_id, m.weight
            FROM entity_category_map m
            JOIN macro_entities e ON e.entity_id = m.entity_id
            ORDER BY m.category_id, m.weight DESC
            """
        ).fetchall()
        out: Dict[str, List[Tuple[str, float]]] = {}
        for r in rows:
            cid = r["category_id"]
            out.setdefault(cid, []).append((str(r["entity_id"]), float(r["weight"])))
        return out
    finally:
        con.close()


def taxonomy_fallback() -> List[Dict[str, Any]]:
    """If DB empty, serve from in-process seed constants."""
    return [
        {
            "category_id": cid,
            "name": name,
            "color_hex": color,
            "description": desc,
            "entity_count": len(TAXONOMY.get(cid, [])),
        }
        for cid, name, color, desc in CATEGORIES
    ]


def persist_flow_snapshot(
    *,
    interval: str,
    ts: float,
    flow_rows: Sequence[Dict[str, Any]],
    qual_rows: Sequence[Dict[str, Any]],
    entity_qual: Dict[str, Dict[str, Any]],
    qa_rows: Sequence[Dict[str, Any]],
    db_path: str | None = None,
) -> None:
    con = _conn(db_path)
    try:
        cur = con.cursor()
        for fr in flow_rows:
            tm_json = json.dumps(fr.get("top_movers") or [])
            cur.execute(
                """
                INSERT INTO flow_scores (category_id, timestamp, interval, cmf, rs_ratio, rs_momentum, flow_score, confidence, top_movers_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(category_id, timestamp, interval) DO UPDATE SET
                    cmf=excluded.cmf, rs_ratio=excluded.rs_ratio, rs_momentum=excluded.rs_momentum,
                    flow_score=excluded.flow_score, confidence=excluded.confidence,
                    top_movers_json=excluded.top_movers_json
                """,
                (
                    fr["category_id"],
                    ts,
                    interval,
                    fr.get("cmf"),
                    fr.get("rs_ratio"),
                    fr.get("rs_momentum"),
                    fr.get("flow_score"),
                    fr.get("confidence"),
                    tm_json,
                ),
            )
        for sym, qr in entity_qual.items():
            cur.execute(
                """
                INSERT INTO qual_scores (entity_id, scored_at, moat_rating, management_score, earnings_quality,
                    margin_trend, balance_sheet, overall_qual, fundamental_band, source)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(entity_id, scored_at) DO UPDATE SET
                    overall_qual=excluded.overall_qual, fundamental_band=excluded.fundamental_band,
                    moat_rating=excluded.moat_rating, earnings_quality=excluded.earnings_quality,
                    margin_trend=excluded.margin_trend, balance_sheet=excluded.balance_sheet
                """,
                (
                    sym,
                    ts,
                    int(qr.get("moat_rating") or 0),
                    None,
                    float(qr.get("earnings_quality") or 0.5),
                    float(qr.get("margin_trend") or 0.5),
                    float(qr.get("balance_sheet") or 0.5),
                    float(qr.get("overall_qual") or 0.5),
                    str(qr.get("fundamental_band") or "neutral"),
                    "yfinance_info",
                ),
            )
        for cq in qual_rows:
            cur.execute(
                """
                INSERT INTO category_qual_scores (category_id, scored_at, weighted_qual_score, fundamental_band,
                    moat_wide_pct, coverage_pct)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(category_id, scored_at) DO UPDATE SET
                    weighted_qual_score=excluded.weighted_qual_score, fundamental_band=excluded.fundamental_band,
                    moat_wide_pct=excluded.moat_wide_pct, coverage_pct=excluded.coverage_pct
                """,
                (
                    cq["category_id"],
                    ts,
                    cq.get("weighted_qual_score"),
                    cq.get("fundamental_band"),
                    cq.get("moat_wide_pct"),
                    cq.get("coverage_pct"),
                ),
            )
        for qa in qa_rows:
            did = qa.get("decision_id") or f"qa_{uuid.uuid4().hex[:16]}"
            cur.execute(
                """
                INSERT INTO flow_qa_decisions (decision_id, category_id, timestamp, interval, quant_flow_score,
                    qual_node_score, qa_verdict, confidence, conflict_flag, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    quant_flow_score=excluded.quant_flow_score, qual_node_score=excluded.qual_node_score,
                    qa_verdict=excluded.qa_verdict, confidence=excluded.confidence, conflict_flag=excluded.conflict_flag,
                    notes=excluded.notes
                """,
                (
                    did,
                    qa["category_id"],
                    ts,
                    interval,
                    qa.get("quant_flow_score"),
                    qa.get("qual_node_score"),
                    qa.get("qa_verdict"),
                    qa.get("confidence"),
                    1 if qa.get("conflict_flag") else 0,
                    qa.get("notes"),
                ),
            )
        con.commit()
    except Exception as e:
        logger.warning("[macro_flow] persist failed: %s", e)
        con.rollback()
    finally:
        con.close()


def load_graph_edges(db_path: str | None = None) -> List[Dict[str, Any]]:
    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT edge_id, source_category, target_category, relationship_type, lag_days, base_strength, description FROM graph_edges"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def persist_edge_flows(
    rows: Sequence[Dict[str, Any]],
    *,
    interval: str,
    ts: float,
    db_path: str | None = None,
) -> None:
    con = _conn(db_path)
    try:
        cur = con.cursor()
        for r in rows:
            cur.execute(
                """
                INSERT INTO edge_flows (edge_id, timestamp, interval, flow_magnitude, direction, confidence)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(edge_id, timestamp, interval) DO UPDATE SET
                    flow_magnitude=excluded.flow_magnitude, direction=excluded.direction, confidence=excluded.confidence
                """,
                (
                    r["edge_id"],
                    ts,
                    interval,
                    r.get("flow_magnitude"),
                    int(r.get("direction") or 0),
                    r.get("confidence"),
                ),
            )
        con.commit()
    except Exception as e:
        logger.warning("[macro_flow] edge_flows persist failed: %s", e)
        con.rollback()
    finally:
        con.close()


def latest_rrg_payload(interval: str, db_path: str | None = None) -> List[Dict[str, Any]]:
    """Join latest flow_scores with category_qual_scores and flow_qa for interval."""
    con = _conn(db_path)
    try:
        # latest timestamp for this interval in flow_scores
        row = con.execute(
            "SELECT MAX(timestamp) AS ts FROM flow_scores WHERE interval=?",
            (interval,),
        ).fetchone()
        ts = row["ts"] if row else None
        if ts is None:
            return []
        flows = con.execute(
            "SELECT * FROM flow_scores WHERE interval=? AND timestamp=?",
            (interval, ts),
        ).fetchall()
        qa = {
            r["category_id"]: dict(r)
            for r in con.execute(
                "SELECT * FROM flow_qa_decisions WHERE interval=? AND timestamp=?",
                (interval, ts),
            ).fetchall()
        }
        qual = {
            r["category_id"]: dict(r)
            for r in con.execute(
                "SELECT * FROM category_qual_scores WHERE scored_at=?",
                (ts,),
            ).fetchall()
        }
        meta = {
            r["category_id"]: dict(r)
            for r in con.execute("SELECT category_id, name, color_hex FROM macro_categories").fetchall()
        }
        out: List[Dict[str, Any]] = []
        for f in flows:
            cid = f["category_id"]
            qrow = qa.get(cid) or {}
            crow = qual.get(cid) or {}
            m = meta.get(cid) or {}
            tm_raw = None
            try:
                tm_raw = f["top_movers_json"]
            except (KeyError, IndexError):
                pass
            top_movers: List[Any] = []
            if tm_raw:
                try:
                    top_movers = json.loads(tm_raw)
                except json.JSONDecodeError:
                    top_movers = []
            out.append(
                {
                    "category_id": cid,
                    "name": m.get("name", cid),
                    "color_hex": m.get("color_hex", "#6366f1"),
                    "rs_ratio": f["rs_ratio"],
                    "rs_momentum": f["rs_momentum"],
                    "flow_score": f["flow_score"],
                    "cmf": f["cmf"],
                    "fundamental_band": crow.get("fundamental_band") or "neutral",
                    "weighted_qual_score": crow.get("weighted_qual_score"),
                    "qa_verdict": qrow.get("qa_verdict") or "watch",
                    "confidence": qrow.get("confidence") or f["confidence"],
                    "conflict_flag": bool(qrow.get("conflict_flag")),
                    "notes": qrow.get("notes") or "",
                    "top_movers": top_movers if isinstance(top_movers, list) else [],
                }
            )
        return out
    finally:
        con.close()


def latest_edge_flows(interval: str, db_path: str | None = None) -> List[Dict[str, Any]]:
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT MAX(timestamp) AS ts FROM edge_flows WHERE interval=?",
            (interval,),
        ).fetchone()
        ts = row["ts"] if row else None
        if ts is None:
            return []
        rows = con.execute(
            """
            SELECT ef.edge_id, ef.flow_magnitude, ef.direction, ef.confidence,
                   ge.source_category, ge.target_category, ge.description
            FROM edge_flows ef
            JOIN graph_edges ge ON ge.edge_id = ef.edge_id
            WHERE ef.interval=? AND ef.timestamp=?
            """,
            (interval, ts),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def flow_timeline(interval: str, limit: int = 30, db_path: str | None = None) -> List[Dict[str, Any]]:
    """Distinct snapshots (timestamp) with per-category flow_score."""
    con = _conn(db_path)
    try:
        ts_rows = con.execute(
            """
            SELECT DISTINCT timestamp FROM flow_scores
            WHERE interval=? ORDER BY timestamp DESC LIMIT ?
            """,
            (interval, max(1, min(200, limit))),
        ).fetchall()
        ts_list = [float(r["timestamp"]) for r in reversed(ts_rows)]
        out: List[Dict[str, Any]] = []
        for ts in ts_list:
            flows = con.execute(
                "SELECT category_id, flow_score, cmf FROM flow_scores WHERE interval=? AND timestamp=?",
                (interval, ts),
            ).fetchall()
            out.append(
                {
                    "timestamp": ts,
                    "scores": {r["category_id"]: float(r["flow_score"]) for r in flows},
                    "cmf": {r["category_id"]: float(r["cmf"] or 0.0) for r in flows},
                }
            )
        return out
    finally:
        con.close()


def value_chain_payload(theme: str, interval: str, db_path: str | None = None) -> Dict[str, Any]:
    """Edges touching ``theme`` (e.g. ai-infra -> ai_infra) with latest propagated flows."""
    tid = (theme or "").replace("-", "_").strip().lower()
    edges = load_graph_edges(db_path)
    touched = [
        e
        for e in edges
        if e.get("source_category") == tid or e.get("target_category") == tid
    ]
    ef = latest_edge_flows(interval, db_path)
    by_eid = {r["edge_id"]: r for r in ef}
    links: List[Dict[str, Any]] = []
    nodes: set[str] = set()
    for e in touched:
        eid = e.get("edge_id") or ""
        row = by_eid.get(eid, {})
        src, tgt = e.get("source_category"), e.get("target_category")
        if src:
            nodes.add(str(src))
        if tgt:
            nodes.add(str(tgt))
        links.append(
            {
                "source": src,
                "target": tgt,
                "edge_id": eid,
                "flow_magnitude": row.get("flow_magnitude"),
                "description": e.get("description"),
            }
        )
    return {"theme": tid, "interval": interval, "nodes": sorted(nodes), "links": links}

