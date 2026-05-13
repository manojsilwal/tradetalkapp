"""
Run macro_flow pipeline: OHLCV + CMF/RS, qual, QA, persist, optional ledger/RAG/CORAL.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List

from .flow_data import fetch_ohlcv_batch
from .flow_qa_verifier import verify_flow_qa
from .graph_propagation import compute_edge_flow_rows
from .macro_flow_agent import aggregate_category_flow
from .qual_node_agent import aggregate_category_qual, fetch_entity_qual_scores
from .store import (
    load_category_weights,
    load_graph_edges,
    persist_edge_flows,
    persist_flow_snapshot,
)
from .taxonomy.seed_taxonomy import TAXONOMY
from .weights_config import get_macro_flow_blend_weights

logger = logging.getLogger(__name__)

_INTERVAL_TO_HORIZON = {"1d": "1d", "1w": "5d", "1m": "21d", "1y": "63d"}


def _weights_map(db_path: str | None) -> Dict[str, List[tuple]]:
    m = load_category_weights(db_path)
    if m:
        return m
    return {k: list(v) for k, v in TAXONOMY.items()}


async def run_macro_flow_pipeline(
    interval: str,
    *,
    db_path: str | None = None,
    emit_ledger: bool = True,
    knowledge_store: Any = None,
) -> Dict[str, Any]:
    """Compute + persist one snapshot for ``interval`` (1d|1w|1m|1y)."""
    iv = interval.strip().lower()
    weights = _weights_map(db_path)
    all_syms = sorted({s for rows in weights.values() for s, _ in rows})
    all_syms_with_spy = sorted(set(all_syms + ["SPY"]))

    frames, entity_qual = await asyncio.gather(
        fetch_ohlcv_batch(all_syms_with_spy, iv),
        fetch_entity_qual_scores(all_syms),
    )
    spy = frames.get("SPY", frames.get("SPY".upper()))
    if spy is None:
        for k, v in frames.items():
            if k.upper() == "SPY":
                spy = v
                break

    flow_rows: List[Dict[str, Any]] = []
    category_scores: Dict[str, float] = {}
    for cid, wts in weights.items():
        row = aggregate_category_flow(cid, wts, frames, spy if spy is not None else {})
        flow_rows.append(row)
        category_scores[cid] = float(row["flow_score"])

    qual_rows: List[Dict[str, Any]] = []
    for cid, wts in weights.items():
        cq = aggregate_category_qual(wts, entity_qual)
        cq["category_id"] = cid
        qual_rows.append(cq)

    qa_rows: List[Dict[str, Any]] = []
    ts = time.time()
    for fr, cq in zip(flow_rows, qual_rows):
        qa = verify_flow_qa(
            flow_score=float(fr["flow_score"]),
            weighted_qual=float(cq.get("weighted_qual_score") or 0.5),
            fundamental_band=str(cq.get("fundamental_band") or "neutral"),
        )
        qa_rows.append(
            {
                "decision_id": f"mfqa_{iv}_{fr['category_id']}_{int(ts)}",
                "category_id": fr["category_id"],
                "quant_flow_score": fr["flow_score"],
                "qual_node_score": cq.get("weighted_qual_score"),
                **qa,
            }
        )

    edges = load_graph_edges(db_path)
    edge_rows = compute_edge_flow_rows(edges, category_scores)
    persist_edge_flows(edge_rows, interval=iv, ts=ts, db_path=db_path)

    persist_flow_snapshot(
        interval=iv,
        ts=ts,
        flow_rows=flow_rows,
        qual_rows=qual_rows,
        entity_qual=entity_qual,
        qa_rows=qa_rows,
        db_path=db_path,
    )

    if emit_ledger:
        _emit_ledger_and_memory(
            interval=iv,
            horizon=_INTERVAL_TO_HORIZON.get(iv, "none"),
            qa_rows=qa_rows,
            flow_rows=flow_rows,
            knowledge_store=knowledge_store,
        )

    try:
        from ..coral_hub import add_note

        add_note(
            "macro_flow",
            f"[macro_flow] refreshed interval={iv} categories={len(flow_rows)} ts={int(ts)}",
            market_regime="",
            ttl_seconds=86400.0 * 3,
        )
    except Exception as e:
        logger.debug("[macro_flow] coral note skipped: %s", e)

    try:
        from ..coral_dreaming import EVENT_MACRO_FLOW
        from ..coral_hub import log_handoff_event

        by_verdict: Dict[str, int] = {}
        for q in qa_rows:
            v = str(q.get("qa_verdict") or "watch")
            by_verdict[v] = by_verdict.get(v, 0) + 1
        top_cat = ""
        top_fs = -999.0
        for r in flow_rows:
            fs = float(r.get("flow_score") or 0.0)
            if fs > top_fs:
                top_fs = fs
                top_cat = str(r.get("category_id") or "")
        log_handoff_event(
            EVENT_MACRO_FLOW,
            {
                "interval": iv,
                "categories": len(flow_rows),
                "edges": len(edge_rows),
                "verdict_counts": by_verdict,
                "strongest_flow_category": top_cat,
                "strongest_flow_score": top_fs,
            },
        )
    except Exception as e:
        logger.debug("[macro_flow] handoff event skipped: %s", e)

    return {"interval": iv, "timestamp": ts, "categories": len(flow_rows), "edges": len(edge_rows)}


def _emit_ledger_and_memory(
    *,
    interval: str,
    horizon: str,
    qa_rows: List[Dict[str, Any]],
    flow_rows: List[Dict[str, Any]],
    knowledge_store: Any,
) -> None:
    from ..decision_ledger import EvidenceRef, FeatureValue, emit_decision

    min_conf = float(get_macro_flow_blend_weights().get("regime_memory_confidence_min", 0.65))

    evidence_list: List[EvidenceRef] = []
    if knowledge_store is not None:
        try:
            docs, refs = knowledge_store.query_with_refs(
                "macro_regime_memories",
                f"Thematic macro flow {interval} regime",
                n_results=2,
            )
            for i, ref in enumerate(refs or []):
                cid = ref.get("chunk_id") or ref.get("id") or ""
                if cid:
                    dist = ref.get("distance")
                    rel = (1.0 - float(dist)) if dist is not None else None
                    evidence_list.append(
                        EvidenceRef(
                            chunk_id=str(cid),
                            collection="macro_regime_memories",
                            relevance=rel,
                            rank=int(ref.get("rank", i)),
                        )
                    )
        except Exception as e:
            logger.debug("[macro_flow] RAG refs skipped: %s", e)

    flow_by_cat = {r["category_id"]: r for r in flow_rows}

    snap_id = ""
    prompt_versions: Dict[str, str] = {}
    try:
        from ..resource_registry import get_resource_registry, registry_enabled

        if registry_enabled():
            reg = get_resource_registry()
            snap_id = reg.snapshot_id()
            prompt_versions = {r.name: r.version for r in reg.list()}
    except Exception:
        pass

    for qa in qa_rows:
        cid = qa["category_id"]
        conf = float(qa.get("confidence") or 0.0)
        try:
            emit_decision(
                decision_type="macro_flow_signal",
                symbol="",
                horizon_hint=horizon,
                verdict=str(qa.get("qa_verdict") or ""),
                confidence=conf,
                output={
                    "category_id": cid,
                    "interval": interval,
                    "quant_flow_score": qa.get("quant_flow_score"),
                    "qual_node_score": qa.get("qual_node_score"),
                    "notes": qa.get("notes"),
                    "conflict_flag": qa.get("conflict_flag"),
                    "cmf": (flow_by_cat.get(cid) or {}).get("cmf"),
                    "rs_momentum": (flow_by_cat.get(cid) or {}).get("rs_momentum"),
                    "top_movers": (flow_by_cat.get(cid) or {}).get("top_movers") or [],
                },
                source_route="/macro/flow/refresh",
                evidence=evidence_list if evidence_list else None,
                features=[
                    FeatureValue(name="flow_score", value_num=float(qa.get("quant_flow_score") or 0.0)),
                    FeatureValue(name="qual_score", value_num=float(qa.get("qual_node_score") or 0.0)),
                ],
                decision_id=str(qa.get("decision_id") or "")[:128] or None,
                prompt_versions=prompt_versions,
                registry_snapshot_id=snap_id,
            )
        except Exception as e:
            logger.debug("[macro_flow] ledger emit skipped row: %s", e)

        if knowledge_store is not None and conf >= min_conf:
            try:
                fr = flow_by_cat.get(cid) or {}
                doc = (
                    f"Macro regime memory [{interval}] {cid}: verdict={qa.get('qa_verdict')} "
                    f"conf={conf:.2f} flow={fr.get('flow_score')} cmf={fr.get('cmf')} "
                    f"notes={qa.get('notes')}"
                )
                knowledge_store.add_macro_regime_memory(
                    text=doc,
                    category_id=cid,
                    interval=interval,
                    verdict=str(qa.get("qa_verdict") or ""),
                    confidence=conf,
                )
            except Exception as e:
                logger.debug("[macro_flow] regime memory write skipped: %s", e)


async def run_macro_flow_pipeline_safe(interval: str, **kw: Any) -> Dict[str, Any]:
    try:
        return await run_macro_flow_pipeline(interval, **kw)
    except Exception as e:
        logger.warning("[macro_flow] pipeline failed: %s", e)
        return {"error": str(e), "interval": interval}
