"""
Durable Knowledge Layer / Ingestion Agent.
Handles capturing, raw archiving, scoring (Stage A + Stage B),
idempotent structured/vector dual-store writing, and point-in-time retrieval.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, date, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# GCS settings
GCS_BUCKET = os.environ.get("GCS_BUCKET", "tradetalk-data-lake")
LOCAL_RAW_DIR = os.environ.get("LOCAL_RAW_DIR", "./data_lake_output/rag/raw")

# Background event queue for async processing
INGESTION_QUEUE: asyncio.Queue = asyncio.Queue()
_worker_task: Optional[asyncio.Task] = None


class IngestionCandidate:
    """Schema for IngestionCandidate events."""

    def __init__(
        self,
        candidate_id: str,
        source_type: str,
        triggered_by: str,
        symbols: List[str],
        as_of_ts: str,
        raw_payload_ref: str,
        payload_summary: str,
        feed_source: str,
        user_id: Optional[str] = None,
    ):
        self.candidate_id = candidate_id
        self.source_type = source_type
        self.triggered_by = triggered_by
        self.symbols = [s.strip().upper() for s in symbols if s]
        self.as_of_ts = as_of_ts
        self.raw_payload_ref = raw_payload_ref
        self.payload_summary = payload_summary
        self.feed_source = feed_source or "unknown"
        self.user_id = user_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_type": self.source_type,
            "triggered_by": self.triggered_by,
            "symbols": self.symbols,
            "as_of_ts": self.as_of_ts,
            "raw_payload_ref": self.raw_payload_ref,
            "payload_summary": self.payload_summary,
            "feed_source": self.feed_source,
            "user_id": self.user_id,
        }


def _archive_raw_payload(source_type: str, candidate_id: str, raw_payload: Any) -> str:
    """
    Saves raw payload to GCS: gs://{GCS_BUCKET}/rag/raw/{source_type}/dt={YYYY-MM-DD}/{candidate_id}.json
    Falls back to local file storage: {LOCAL_RAW_DIR}/{source_type}/dt={YYYY-MM-DD}/{candidate_id}.json
    """
    dt_str = datetime.now(timezone.utc).date().isoformat()
    filename = f"{candidate_id}.json"
    
    # 1. Local archive (always save locally for provenance/fallback)
    local_dir = os.path.join(LOCAL_RAW_DIR, source_type, f"dt={dt_str}")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, filename)
    
    try:
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(raw_payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[IngestionAgent] Failed to write local raw payload archive: %s", e)

    # 2. Upload to GCS if available
    gcs_prefix = f"rag/raw/{source_type}/dt={dt_str}"
    gcs_path = f"gs://{GCS_BUCKET}/{gcs_prefix}/{filename}"
    
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(f"{gcs_prefix}/{filename}")
        blob.upload_from_string(json.dumps(raw_payload, ensure_ascii=False), content_type="application/json")
        logger.info("[IngestionAgent] Uploaded raw payload to GCS: %s", gcs_path)
        return gcs_path
    except Exception as e:
        logger.debug("[IngestionAgent] GCS upload skipped or failed (falling back to local): %s", e)
        return local_path


async def emit_ingestion_candidate(
    source_type: str,
    symbols: List[str],
    triggered_by: str,
    raw_payload: Any,
    user_id: Optional[str] = None,
    feed_source: Optional[str] = None,
    as_of_ts: Optional[str] = None,
) -> IngestionCandidate:
    """
    Entry point to emit an ingestion candidate event off the request path.
    Calculates deterministic hash, archives raw payload, and queues event.
    """
    as_of = as_of_ts or datetime.now(timezone.utc).isoformat()
    
    # Generate deterministic candidate ID hash
    payload_str = json.dumps(raw_payload, sort_keys=True, default=str)
    payload_hash = hashlib.md5(payload_str.encode("utf-8")).hexdigest()
    candidate_raw_id = f"{source_type}:{payload_hash}:{as_of}"
    candidate_id = hashlib.md5(candidate_raw_id.encode("utf-8")).hexdigest()

    # Build summary
    summary = ""
    if source_type == "single_stock_search":
        summary = f"Search trace for {','.join(symbols)}"
    elif source_type == "daily_brief":
        summary = f"S&P 500 Daily brief batch with {len(symbols)} symbols"
    elif source_type == "macro_pull":
        summary = f"Macro pull data indicators"
    elif source_type == "capital_flow_pull":
        summary = f"Capital flow reconciliation details"
        
    # Archive raw payload
    raw_payload_ref = await asyncio.to_thread(_archive_raw_payload, source_type, candidate_id, raw_payload)

    candidate = IngestionCandidate(
        candidate_id=candidate_id,
        source_type=source_type,
        triggered_by=triggered_by,
        symbols=symbols,
        as_of_ts=as_of,
        raw_payload_ref=raw_payload_ref,
        payload_summary=summary[:500],
        feed_source=feed_source or "unknown",
        user_id=user_id,
    )

    # Queue for async processing
    await INGESTION_QUEUE.put((candidate, raw_payload))
    logger.info("[IngestionAgent] Queued candidate %s from %s", candidate_id, source_type)
    return candidate


def _delete_existing_keys(table: str, keys: Dict[str, Any]):
    """Helper to delete rows with specified keys from BigQuery/DuckDB backend."""
    from .mcp_server.backend import backend
    conds = []
    for k, v in keys.items():
        if isinstance(v, str):
            conds.append(f"{k} = '{v}'")
        else:
            conds.append(f"{k} = {v}")
    where_clause = " AND ".join(conds)
    sql = f"DELETE FROM {table} WHERE {where_clause}"
    try:
        backend().execute(sql)
    except Exception as e:
        logger.warning("[IngestionAgent] Failed to delete existing keys in %s: %s", table, e)


def _upsert_structured_fact(table: str, row: Dict[str, Any], keys: List[str]):
    """Upsert structured record into BQ/DuckDB by running DELETE then INSERT."""
    from .mcp_server.backend import backend
    key_map = {k: row[k] for k in keys if k in row}
    if key_map:
        _delete_existing_keys(table, key_map)
    try:
        backend().insert_rows(table, [row])
    except Exception as e:
        logger.warning("[IngestionAgent] Upsert failed for table %s: %s", table, e)


async def _check_vector_duplicates(
    ks, ticker: str, text: str, as_of_date: date, threshold: float = 0.92
) -> bool:
    """
    Returns True if a similar vector exists for the same ticker within a +/- 3 day window.
    Cosine similarity > 0.92 translates to cosine distance < 0.08.
    """
    col = ks._safe_col("rag_chunks")
    if not col:
        return False
        
    try:
        # Search the vector store
        where_filter = {"ticker": ticker} if ticker else None
        res = col.query(query_texts=[text], n_results=5, where=where_filter)
        
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        
        for doc, meta, dist in zip(docs, metas, dists):
            # Parse hit date
            hit_date_str = meta.get("flow_date") or meta.get("as_of_ts")
            if hit_date_str:
                try:
                    hit_date = datetime.fromisoformat(str(hit_date_str)[:10]).date()
                    date_diff = abs((as_of_date - hit_date).days)
                    # Check if date is within 3 days and similarity is high
                    if date_diff <= 3 and dist < (1.0 - threshold):
                        logger.info("[IngestionAgent] Duplicate chunk detected: %s (distance %.4f)", doc[:50], dist)
                        return True
                except Exception:
                    pass
    except Exception as e:
        logger.warning("[IngestionAgent] Vector duplicate check failed: %s", e)
    return False


async def process_candidate(candidate: IngestionCandidate, raw_payload: Any) -> None:
    """
    Processes a single candidate event:
    1. Score worthiness (Stage A deterministic rules).
    2. Score worthiness (Stage B LLM judge).
    3. Normalize and dual-store writes.
    """
    from .deps import knowledge_store, llm_client
    
    source = candidate.source_type
    symbols = candidate.symbols
    as_of_dt = datetime.fromisoformat(candidate.as_of_ts.replace("Z", "+00:00"))
    as_of_date_str = as_of_dt.date().isoformat()
    now_ts = datetime.now(timezone.utc).isoformat()
    
    logger.info("[IngestionAgent] Processing candidate %s (source=%s)", candidate.candidate_id, source)

    # 1. Update Symbol Interest Counter (always done for search interest metrics)
    if source == "single_stock_search" and symbols:
        for sym in symbols:
            await _update_symbol_interest(sym, candidate.user_id)

    # --- STAGE A: Deterministic Worthiness Filter ---
    passed_stage_a = False
    stage_a_reason = "Discarded by Stage A rules."
    
    extracted_records: List[Dict[str, Any]] = []
    extracted_chunks: List[Tuple[str, Dict[str, Any]]] = [] # (text, metadata)

    if source == "single_stock_search":
        # Extract from trace/debate output
        verdict = raw_payload.get("global_verdict") or raw_payload.get("verdict") or "NEUTRAL"
        signal = raw_payload.get("global_signal") or 0
        conf = raw_payload.get("confidence") or raw_payload.get("consensus_confidence") or 0.5
        rationale = raw_payload.get("consensus_rationale") or raw_payload.get("moderator_summary") or ""
        
        # Keep if significant move or non-neutral consensus
        is_significant = abs(float(signal)) > 0 or conf > 0.7
        if is_significant:
            passed_stage_a = True
            stage_a_reason = f"Significant search trace verdict: {verdict}"
            
            # Form structured fact
            for sym in symbols:
                extracted_records.append({
                    "table": "rag_price_facts",
                    "row": {
                        "symbol": sym,
                        "trade_date": as_of_date_str,
                        "daily_return_pct": float(signal) * 5.0,  # approximate move proxy
                        "return_zscore_60d": float(signal) * 2.0,
                        "relative_volume": 1.5,
                        "close": 0.0,
                        "volume": 0,
                        "ingested_at": now_ts,
                    },
                    "keys": ["symbol", "trade_date"]
                })
            
            # Form narrative chunk
            if rationale:
                extracted_chunks.append((
                    f"[{source}] Tickers: {','.join(symbols)}. Verdict: {verdict}. Rationale: {rationale}",
                    {
                        "as_of_ts": candidate.as_of_ts,
                        "symbols": symbols,
                        "category": "consensus",
                        "source_type": source,
                        "reusability": 0.8,
                        "durability": "long_term",
                        "event_id": candidate.candidate_id,
                        "flow_date": as_of_date_str,
                        "ticker": symbols[0] if len(symbols) == 1 else "",
                    }
                ))

    elif source == "daily_brief":
        # Raw payload is a list of rows from the daily brief snap
        rows = raw_payload.get("rows") or raw_payload
        if isinstance(rows, list):
            passed_count = 0
            for r in rows:
                sym = str(r.get("symbol") or "").upper().strip()
                ret = float(r.get("daily_return_pct") or 0.0)
                rel_vol = float(r.get("relative_volume") or 1.0)
                zscore = float(r.get("return_zscore_60d") or 0.0)
                
                # Retrieve symbol search interest count
                interest_count = await _get_symbol_interest_count(sym)
                
                # Rule: absolute return > 3.5% OR z-score > 2.0 OR rel volume > 2.5 OR interest > 5
                is_big_move = abs(ret) > 3.5 or abs(zscore) > 2.0 or rel_vol > 2.5 or interest_count > 5
                if is_big_move:
                    passed_stage_a = True
                    passed_count += 1
                    
                    extracted_records.append({
                        "table": "rag_price_facts",
                        "row": {
                            "symbol": sym,
                            "trade_date": as_of_date_str,
                            "daily_return_pct": ret,
                            "return_zscore_60d": zscore,
                            "relative_volume": rel_vol,
                            "close": float(r.get("close") or 0.0),
                            "volume": int(r.get("volume") or 0),
                            "ingested_at": now_ts,
                        },
                        "keys": ["symbol", "trade_date"]
                    })
                    
                    reason = r.get("one_line_reason") or r.get("verdict")
                    if reason:
                        extracted_chunks.append((
                            f"[{source}] Symbol {sym} moved {ret:+.2f}% on {as_of_date_str}. Cause: {reason}",
                            {
                                "as_of_ts": candidate.as_of_ts,
                                "symbols": [sym],
                                "category": "price_mover",
                                "source_type": source,
                                "reusability": 0.7,
                                "durability": "long_term",
                                "event_id": candidate.candidate_id,
                                "flow_date": as_of_date_str,
                                "ticker": sym,
                            }
                        ))
            stage_a_reason = f"Daily brief processing passed {passed_count} high-signal symbols."

    elif source == "macro_pull":
        # Raw payload contains macro health metrics
        ind = raw_payload.get("indicators") or raw_payload
        if ind:
            passed_stage_a = True
            stage_a_reason = "Macro indicator update received."
            
            # Map FRED/yFinance values
            for key in ("fed_funds_rate", "cpi_yoy", "treasury_10y", "unemployment"):
                val = ind.get(key)
                if val is not None:
                    extracted_records.append({
                        "table": "rag_macro_facts",
                        "row": {
                            "release_name": key.upper(),
                            "release_date": as_of_date_str,
                            "actual_value": float(val),
                            "consensus_value": None,
                            "prior_value": None,
                            "surprise_sign": "neutral",
                            "ingested_at": now_ts,
                        },
                        "keys": ["release_name", "release_date"]
                    })
            
            narrative = ind.get("macro_narrative") or ""
            if narrative:
                extracted_chunks.append((
                    f"[Macro Snapshot] Outlook narrative on {as_of_date_str}: {narrative}",
                    {
                        "as_of_ts": candidate.as_of_ts,
                        "symbols": [],
                        "category": "macro_narrative",
                        "source_type": source,
                        "reusability": 0.9,
                        "durability": "long_term",
                        "event_id": candidate.candidate_id,
                        "flow_date": as_of_date_str,
                        "ticker": "",
                    }
                ))

    elif source == "capital_flow_pull":
        # Reconciled capital flows snapshot
        flow_data = raw_payload.get("reconciled") or raw_payload
        if flow_data:
            recon = flow_data.get("reconciliation") or {}
            is_reconciled = recon.get("is_reconciled") or False
            net_change = abs(recon.get("net_capital_change_usd") or 0.0)
            
            # Keep if reconciled OR net flow > 5 Billion
            if is_reconciled or net_change > 5_000_000_000:
                passed_stage_a = True
                stage_a_reason = f"Capital flows reconciled={is_reconciled}, net_change={net_change}"
                
                extracted_records.append({
                    "table": "rag_flow_snapshots",
                    "row": {
                        "flow_date": as_of_date_str,
                        "opening_capital_total_usd": float(recon.get("opening_capital_total_usd") or 0),
                        "closing_capital_total_usd": float(recon.get("closing_capital_total_usd") or 0),
                        "net_capital_change_usd": float(recon.get("net_capital_change_usd") or 0),
                        "reconciliation_gap_usd": float(recon.get("reconciliation_gap_usd") or 0),
                        "is_reconciled": bool(is_reconciled),
                        "us_net_increased": bool(recon.get("us_net_increased")),
                        "tolerance_usd": float(recon.get("tolerance_usd") or 1.0),
                        "raw_payload_json": json.dumps(flow_data),
                        "ingested_at": now_ts,
                    },
                    "keys": ["flow_date"]
                })
                
                # Extract description
                exp = flow_data.get("explanation") or {}
                drivers_in = exp.get("drivers_inflow_to_us") or []
                drivers_out = exp.get("drivers_outflow_from_us") or []
                desc = f"Capital flow analysis for {as_of_date_str}. Net capital change: {recon.get('net_capital_change_usd')}. "
                if drivers_in:
                    desc += f"Inflows driven by: {','.join([d['display_name'] for d in drivers_in[:3]])}. "
                if drivers_out:
                    desc += f"Outflows driven by: {','.join([d['display_name'] for d in drivers_out[:3]])}."
                    
                extracted_chunks.append((
                    f"[Capital Flow Flow] {desc}",
                    {
                        "as_of_ts": candidate.as_of_ts,
                        "symbols": ["SPY", "EFA", "EWJ", "TLT", "GLD", "BIL"],
                        "category": "capital_flows",
                        "source_type": source,
                        "reusability": 0.8,
                        "durability": "long_term",
                        "event_id": candidate.candidate_id,
                        "flow_date": as_of_date_str,
                        "ticker": "",
                    }
                ))

    # Log Stage A outcome
    if not passed_stage_a:
        logger.info("[IngestionAgent] Candidate %s discarded by Stage A rules.", candidate.candidate_id)
        _log_ingestion_decision(candidate, "discarded", stage_a_reason)
        return

    # --- STAGE B: LLM Judge Filter ---
    # Submit surviving candidates/chunks to the LLM judge for scoring
    final_records = list(extracted_records)
    final_chunks = []
    
    for text_doc, meta in extracted_chunks:
        # LLM classification
        judge_prompt = f"""
Given this data point and what we already store, decide:
- keep_as: 'structured_fact' | 'narrative_chunk' | 'both' | 'discard'
- reusability: 0..1   (will future backtests/queries likely need this?)
- durability: 'ephemeral' | 'session' | 'long_term'
- tags: [earnings, fed, tariff, mna, rotation, ...]
- linked_symbols: [TICKER1, TICKER2, ...]
- one_line_reason: why it's worth keeping

Return a valid JSON object matching these keys. Do not return markdown tags outside of the JSON block.

Candidate Narrative Text:
{text_doc}
"""
        try:
            # NVDA cascade invocation
            judge_res = await llm_client.generate("ingestion_judge", judge_prompt)
            
            keep_as = judge_res.get("keep_as", "discard")
            reusability = float(judge_res.get("reusability", 0.0))
            durability = judge_res.get("durability", "ephemeral")
            reason = judge_res.get("one_line_reason", "No reason provided")
            tags = judge_res.get("tags") or []
            
            logger.info("[IngestionAgent] Judge verdict: keep_as=%s, reusability=%.2f, reason=%s", keep_as, reusability, reason)
            
            if keep_as != "discard" and reusability >= 0.5:
                # Meta decoration
                meta["reusability"] = reusability
                meta["durability"] = durability
                meta["tags"] = tags
                
                # Add to chunks to write
                final_chunks.append((text_doc, meta))
            else:
                logger.info("[IngestionAgent] Judge discarded chunk: %s", reason)
        except Exception as e:
            logger.warning("[IngestionAgent] LLM judge call failed: %s. Storing by default.", e)
            # Default fallback: store if Stage A passed
            final_chunks.append((text_doc, meta))

    # --- WRITE DUAL STORE & DEDUPLICATE ---
    # 1. Structured DB inserts
    for rec in final_records:
        _upsert_structured_fact(rec["table"], rec["row"], rec["keys"])
        
    # 2. Vector DB inserts with semantic deduplication
    ks = knowledge_store
    col = ks._safe_col("rag_chunks")
    
    if col and final_chunks:
        added_count = 0
        for text_doc, meta in final_chunks:
            ticker = meta.get("ticker", "")
            # Check for duplicate
            is_dup = await _check_vector_duplicates(ks, ticker, text_doc, as_of_dt.date())
            if not is_dup:
                chunk_id = f"chunk_{candidate.candidate_id}_{added_count}_{int(datetime.now(timezone.utc).timestamp())}"
                
                # Supabase handles lists in JSONB metadata nicely.
                # Ensure fields are serialized safely.
                clean_meta = dict(meta)
                # Chroma metadata requires values to be simple (str, int, float, bool)
                # Serialize lists to string to prevent backend-specific index crashes
                clean_meta["symbols"] = ",".join(meta.get("symbols") or [])
                clean_meta["tags"] = ",".join(meta.get("tags") or [])
                
                try:
                    col.add(
                        documents=[text_doc],
                        metadatas=[clean_meta],
                        ids=[chunk_id]
                    )
                    added_count += 1
                except Exception as e:
                    logger.warning("[IngestionAgent] Failed to add vector chunk to store: %s", e)
        logger.info("[IngestionAgent] Wrote %d narrative chunks to rag_chunks vector collection", added_count)

    # Log ingestion choice
    decision = "kept" if (final_records or final_chunks) else "discarded"
    decision_reason = f"Kept {len(final_records)} records and {len(final_chunks)} chunks." if decision == "kept" else "No keepable elements found."
    _log_ingestion_decision(candidate, decision, decision_reason, keep_as="both" if (final_records and final_chunks) else ("structured_fact" if final_records else "narrative_chunk"))


def _log_ingestion_decision(
    candidate: IngestionCandidate,
    decision: str,
    reason: str,
    keep_as: str = "discard",
) -> None:
    """Writes a log row to rag_ingestion_log table."""
    now_ts = datetime.now(timezone.utc).isoformat()
    row = {
        "candidate_id": candidate.candidate_id,
        "source_type": candidate.source_type,
        "triggered_by": candidate.triggered_by,
        "symbols": candidate.symbols,
        "as_of_ts": candidate.as_of_ts,
        "decision": decision,
        "decision_reason": reason,
        "keep_as": keep_as,
        "raw_payload_ref": candidate.raw_payload_ref,
        "agent_version": "1.0",
        "model_version": "kimi-k2.6",
        "created_at": now_ts,
    }
    try:
        from .mcp_server.backend import backend
        backend().insert_rows("rag_ingestion_log", [row])
    except Exception as e:
        logger.warning("[IngestionAgent] Failed to write ingestion decision log: %s", e)


# --- Interest tracking helpers ---

async def _update_symbol_interest(symbol: str, user_id: Optional[str]) -> None:
    """Idempotently updates symbol interest counter in rag_symbol_interest table."""
    from .mcp_server.backend import backend
    sym = symbol.strip().upper()
    if not sym:
        return
        
    now_ts = datetime.now(timezone.utc).isoformat()
    sql = f"SELECT search_count, distinct_users_json FROM rag_symbol_interest WHERE symbol = '{sym}'"
    try:
        rows = backend().query(sql)
        if rows:
            cur = rows[0]
            count = int(cur.get("search_count") or 0) + 1
            users_raw = cur.get("distinct_users_json") or "[]"
            try:
                users = json.loads(users_raw)
            except Exception:
                users = []
            if user_id and user_id not in users:
                users.append(user_id)
            row = {
                "symbol": sym,
                "search_count": count,
                "last_searched": now_ts,
                "distinct_users_json": json.dumps(users)
            }
        else:
            users = [user_id] if user_id else []
            row = {
                "symbol": sym,
                "search_count": 1,
                "last_searched": now_ts,
                "distinct_users_json": json.dumps(users)
            }
        _upsert_structured_fact("rag_symbol_interest", row, ["symbol"])
    except Exception as e:
        logger.warning("[IngestionAgent] Failed to update symbol interest: %s", e)


async def _get_symbol_interest_count(symbol: str) -> int:
    """Returns the search count for a symbol from rag_symbol_interest."""
    from .mcp_server.backend import backend
    sym = symbol.strip().upper()
    if not sym:
        return 0
    sql = f"SELECT search_count FROM rag_symbol_interest WHERE symbol = '{sym}'"
    try:
        rows = backend().query(sql)
        if rows:
            return int(rows[0].get("search_count") or 0)
    except Exception:
        pass
    return 0


# --- Background queue worker task ---

async def _queue_worker_loop() -> None:
    """Loop to process candidate events from INGESTION_QUEUE in background."""
    logger.info("[IngestionAgent] Starting background ingestion worker loop...")
    while True:
        try:
            candidate, raw_payload = await INGESTION_QUEUE.get()
            try:
                await process_candidate(candidate, raw_payload)
            except Exception as ex:
                logger.exception("[IngestionAgent] Error processing queued candidate: %s", ex)
            finally:
                INGESTION_QUEUE.task_done()
        except asyncio.CancelledError:
            logger.info("[IngestionAgent] Worker loop cancelled.")
            break
        except Exception as e:
            logger.exception("[IngestionAgent] Worker loop encountered unexpected error: %s", e)
            await asyncio.sleep(1)


def start_ingestion_worker() -> None:
    """Starts the async queue worker task in the background."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_queue_worker_loop())
        logger.info("[IngestionAgent] Background ingestion worker task created.")


# --- RETRIEVAL API & Point-in-Time Constraint helpers ---

async def retrieveContext(
    query: str,
    symbols: List[str],
    date_range: Optional[Tuple[str, str]] = None,
    mode: str = "semantic",
    decision_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Exposes RAG retrieval with optional metadata filters (symbols, dateRange, category)
    and strict point-in-time constraints (as_of_ts <= decision_time).
    """
    from .deps import knowledge_store
    from .mcp_server.backend import backend
    
    cutoff = decision_time or datetime.now(timezone.utc).isoformat()
    syms = [s.strip().upper() for s in symbols if s]
    
    # 1. Exact Mode (structured queries with point-in-time constraints)
    if mode == "exact":
        price_facts = []
        macro_facts = []
        flow_snaps = []
        
        # Query price facts (partition pruned by trade_date, sorted, <= cutoff)
        if syms:
            sym_list = ",".join([f"'{s}'" for s in syms])
            sql = f"""
                SELECT symbol, trade_date, daily_return_pct, return_zscore_60d, relative_volume, close, volume 
                FROM rag_price_facts 
                WHERE symbol IN ({sym_list}) AND trade_date <= '{cutoff[:10]}' 
                ORDER BY trade_date DESC LIMIT 50
            """
            price_facts = backend().query(sql)
            
        # Query macro facts
        sql = f"""
            SELECT release_name, release_date, actual_value, surprise_sign 
            FROM rag_macro_facts 
            WHERE release_date <= '{cutoff[:10]}' 
            ORDER BY release_date DESC LIMIT 30
        """
        macro_facts = backend().query(sql)
        
        # Query flow snapshots
        sql = f"""
            SELECT flow_date, opening_capital_total_usd, closing_capital_total_usd, net_capital_change_usd, is_reconciled 
            FROM rag_flow_snapshots 
            WHERE flow_date <= '{cutoff[:10]}' 
            ORDER BY flow_date DESC LIMIT 15
        """
        flow_snaps = backend().query(sql)
        
        return {
            "mode": "exact",
            "price_facts": price_facts,
            "macro_facts": macro_facts,
            "flow_snapshots": flow_snaps,
        }
        
    # 2. Semantic Mode (hybrid vector + metadata filters)
    else:
        col = knowledge_store._safe_col("rag_chunks")
        if not col:
            return {"mode": "semantic", "chunks": [], "joined_facts": []}
            
        chunks = []
        try:
            # Query vector store
            # For simplicity, we filter in memory to enforce point-in-time constraints on metadata
            where_filter = None
            if len(syms) == 1:
                where_filter = {"ticker": syms[0]}
                
            res = col.query(query_texts=[query], n_results=12, where=where_filter)
            docs = res.get("documents", [[]])[0]
            metas = res.get("metadatas", [[]])[0]
            dists = res.get("distances", [[]])[0]
            ids = res.get("ids", [[]])[0] if res.get("ids") else []
            
            for doc, meta, dist, chunk_id in zip(docs, metas, dists, ids):
                # Point-in-time check: chunk as_of_ts must be <= cutoff
                chunk_as_of = meta.get("as_of_ts") or meta.get("flow_date")
                if chunk_as_of and chunk_as_of > cutoff:
                    continue
                    
                # Date range filter check
                if date_range:
                    start, end = date_range
                    chunk_date = str(chunk_as_of)[:10]
                    if chunk_date < start or chunk_date > end:
                        continue
                        
                chunks.append({
                    "id": chunk_id,
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                })
        except Exception as e:
            logger.warning("[IngestionAgent] Semantic retrieval failed: %s", e)
            
        # Join structured facts for the matched symbols
        matched_symbols = set(syms)
        for c in chunks:
            syms_str = c["metadata"].get("symbols") or ""
            for s in syms_str.split(","):
                if s.strip():
                    matched_symbols.add(s.strip().upper())
                    
        joined_facts = []
        if matched_symbols:
            sym_list = ",".join([f"'{s}'" for s in matched_symbols if s])
            sql = f"""
                SELECT symbol, trade_date, daily_return_pct, close 
                FROM rag_price_facts 
                WHERE symbol IN ({sym_list}) AND trade_date <= '{cutoff[:10]}' 
                ORDER BY trade_date DESC LIMIT 20
            """
            joined_facts = backend().query(sql)
            
        return {
            "mode": "semantic",
            "chunks": chunks[:8],
            "joined_facts": joined_facts,
        }


async def getSymbolHistory(symbol: str, cutoff: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns price history for a symbol up to a specific cutoff timestamp."""
    from .mcp_server.backend import backend
    sym = symbol.strip().upper()
    if not sym:
        return []
    limit_dt = cutoff or datetime.now(timezone.utc).isoformat()
    sql = f"""
        SELECT symbol, trade_date, daily_return_pct, return_zscore_60d, relative_volume, close, volume 
        FROM rag_price_facts 
        WHERE symbol = '{sym}' AND trade_date <= '{limit_dt[:10]}' 
        ORDER BY trade_date DESC LIMIT 100
    """
    return backend().query(sql)


async def getMacroAround(target_date: str) -> List[Dict[str, Any]]:
    """Returns macro release facts around a target date (+/- 5 days)."""
    from .mcp_server.backend import backend
    try:
        dt = datetime.fromisoformat(target_date[:10]).date()
        start = (dt - timedelta(days=5)).isoformat()
        end = (dt + timedelta(days=5)).isoformat()
        sql = f"""
            SELECT release_name, release_date, actual_value, surprise_sign 
            FROM rag_macro_facts 
            WHERE release_date >= '{start}' AND release_date <= '{end}' 
            ORDER BY release_date DESC
        """
        return backend().query(sql)
    except Exception:
        return []


async def getFlowSnapshot(target_date: str) -> Optional[Dict[str, Any]]:
    """Returns the reconciled capital flow snapshot for a given date."""
    from .mcp_server.backend import backend
    sql = f"SELECT flow_date, raw_payload_json FROM rag_flow_snapshots WHERE flow_date = '{target_date[:10]}'"
    try:
        rows = backend().query(sql)
        if rows:
            payload_raw = rows[0].get("raw_payload_json")
            if payload_raw:
                return json.loads(payload_raw)
    except Exception:
        pass
    return None
