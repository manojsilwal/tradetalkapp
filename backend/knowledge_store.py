"""
Knowledge Store — vector-memory singleton with multiple collections.
Every agent run, debate, backtest, and daily pipeline job writes here.
Agents query this store before every LLM call (RAG).
All data is cumulative — semantic search spans the full history.

Operational policy (TTL, PII, collection purpose) is documented in
``docs/RAG_POLICY.md`` at the repository root — read before changing
ingestion or retention behavior.
"""
import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional
from .vector_backends import ChromaVectorBackend, SupabaseVectorBackend

logger = logging.getLogger(__name__)

# Persist to disk so knowledge survives restarts
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./chroma_db")
VECTOR_BACKEND = os.environ.get("VECTOR_BACKEND", "chroma").lower()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

COLLECTIONS = [
    "swarm_history",                 # every /trace call
    "swarm_reflections",             # daily outcome tracking — swarm learns from results
    "debate_history",                # every /debate call
    "macro_alerts",                  # every 60s news scan (already exists logic)
    "strategy_backtests",            # every /backtest call
    "price_movements",              # daily pipeline — top movers
    "macro_snapshots",               # daily pipeline — FRED indicators
    "youtube_insights",              # daily pipeline — finance channel videos
    "strategy_reflections",          # post-backtest lessons learned memory
    "stock_profiles",                # data lake — 15yr per-ticker narrative profiles
    "earnings_memory",               # data lake — earnings events with price reactions
    "sp500_fundamentals_narratives", # S&P 500 daily fundamental snapshots as text narratives
    "sp500_sector_analysis",         # S&P 500 weekly sector rotation + momentum narratives
    "chat_memories",                 # salient chat exchanges stored for cross-session recall
]

class _CollectionProxy:
    """Uniform collection interface over selected vector backend."""

    def __init__(self, backend, name: str):
        self._backend = backend
        self._name = name

    def add(self, documents, metadatas, ids, embeddings=None):
        self._backend.add(self._name, documents=documents, metadatas=metadatas, ids=ids, embeddings=embeddings)

    def query(self, query_texts, n_results, where=None):
        rows = self._backend.query(
            self._name,
            query_text=query_texts[0],
            n_results=n_results,
            where=where,
        )
        # Match Chroma shape for existing callsites.
        return {
            "documents": [rows.get("documents", [])],
            "metadatas": [rows.get("metadatas", [])],
            "ids": [rows.get("ids", [])],
            "distances": [rows.get("distances", [])],
        }

    def get(self, include=None):
        rows = self._backend.get(self._name)
        return rows

    def count(self):
        return self._backend.count(self._name)


class KnowledgeStore:
    """
    Singleton ChromaDB wrapper.
    Uses PersistentClient so all knowledge survives server restarts.
    """

    def __init__(self):
        self._client = None
        self._vector_backend = None
        self._active_vector_backend = "chroma"
        self._cols: dict = {}
        self._retrieval_default_top_k = int(os.environ.get("RAG_TOP_K", "5"))
        self._retrieval_max_top_k = int(os.environ.get("RAG_TOP_K_MAX", "12"))
        self._pipeline_status: dict = {
            "last_run": None,
            "price_movements_added": 0,
            "youtube_videos_added": 0,
            "macro_snapshot_added": False,
            "total_collection_sizes": {},
        }
        self._init_vector_backend()

    def _init_vector_backend(self):
        try:
            if VECTOR_BACKEND == "hf":
                from huggingface_hub import hf_hub_download
                import chromadb
                hf_id = os.environ.get("HF_DATASET_ID", "")
                if not hf_id:
                    raise RuntimeError("HF_DATASET_ID missing for VECTOR_BACKEND=hf")
                
                logger.info(f"[KnowledgeStore] Downloading all_summaries.json from HF {hf_id}...")
                token = os.environ.get("HF_TOKEN")
                file_path = hf_hub_download(repo_id=hf_id, repo_type="dataset", filename="rag_summaries/all_summaries.json", token=token)
                with open(file_path, "r") as f:
                    summaries = json.load(f)
                    
                self._vector_backend = ChromaVectorBackend(chroma_path=None) 
                self._active_vector_backend = "hf"
                
                for name in COLLECTIONS:
                    self._vector_backend.ensure_collection(name)
                    self._cols[name] = _CollectionProxy(self._vector_backend, name)
                
                by_collection = {}
                for s in summaries:
                    by_collection.setdefault(s.get("collection", "unknown"), []).append(s)
                
                for col_name, items in by_collection.items():
                    if col_name in self._cols:
                        docs = [s["document"] for s in items]
                        metas = [s.get("metadata", {}) for s in items]
                        ids = [s["id"] for s in items]
                        embeddings = [s.get("embedding") for s in items]
                        
                        for m in metas:
                            for k, v in list(m.items()):
                                if v is None: m[k] = ""
                                elif isinstance(v, (list, dict)): m[k] = json.dumps(v)
                                
                        if all(e is None for e in embeddings):
                            embeddings = None
                            
                        self._cols[col_name].add(documents=docs, metadatas=metas, ids=ids, embeddings=embeddings)
                
                backend_name = "Hugging Face (In-Memory)"

            elif VECTOR_BACKEND == "supabase":
                if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
                    raise RuntimeError("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY missing for VECTOR_BACKEND=supabase")
                self._vector_backend = SupabaseVectorBackend(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
                backend_name = "Supabase"
                self._active_vector_backend = "supabase"
            else:
                self._vector_backend = ChromaVectorBackend(chroma_path=CHROMA_PATH)
                backend_name = "ChromaDB"
                self._active_vector_backend = "chroma"
                
            if VECTOR_BACKEND != "hf":
                for name in COLLECTIONS:
                    self._vector_backend.ensure_collection(name)
                    self._cols[name] = _CollectionProxy(self._vector_backend, name)
            logger.info(f"[KnowledgeStore] {backend_name} vector backend ready — {len(COLLECTIONS)} collections")
        except Exception as e:
            logger.error(f"[KnowledgeStore] Vector backend init failed ({VECTOR_BACKEND}): {e}")
            if VECTOR_BACKEND != "chroma":
                logger.warning("[KnowledgeStore] Falling back to Chroma backend")
                self._vector_backend = ChromaVectorBackend(chroma_path=CHROMA_PATH)
                self._active_vector_backend = "chroma"
                for name in COLLECTIONS:
                    self._vector_backend.ensure_collection(name)
                    self._cols[name] = _CollectionProxy(self._vector_backend, name)

    def _safe_col(self, name: str):
        return self._cols.get(name)

    # ── WRITE METHODS ─────────────────────────────────────────────────────────

    def add_swarm_analysis(self, consensus) -> None:
        """Store a SwarmConsensus result after /trace."""
        col = self._safe_col("swarm_history")
        if not col:
            return
        try:
            doc = (
                f"Swarm analysis of {consensus.ticker} on {datetime.now(timezone.utc).date()}. "
                f"Verdict: {consensus.global_verdict}. Confidence: {consensus.confidence:.2f}. "
                f"Signal: {consensus.global_signal}. "
                f"Regime: {consensus.macro_state.market_regime}. "
                f"Credit stress: {consensus.macro_state.credit_stress_index:.2f}."
            )
            entry_id = f"swarm_{consensus.ticker}_{int(time.time())}"
            col.add(
                documents=[doc],
                metadatas=[{
                    "ticker": consensus.ticker,
                    "verdict": consensus.global_verdict,
                    "confidence": consensus.confidence,
                    "global_signal": consensus.global_signal,
                    "market_regime": consensus.macro_state.market_regime.value if hasattr(consensus.macro_state.market_regime, 'value') else str(consensus.macro_state.market_regime),
                    "credit_stress": consensus.macro_state.credit_stress_index,
                    "date": str(datetime.now(timezone.utc).date()),
                }],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_swarm_analysis failed: {e}")

    def add_debate(self, result) -> None:
        """Store a DebateResult after /debate."""
        col = self._safe_col("debate_history")
        if not col:
            return
        try:
            agent_summary = "; ".join(
                f"{a.agent_role}({a.stance.value}): {a.headline}"
                for a in result.arguments
            )
            doc = (
                f"AI debate on {result.ticker} on {datetime.now(timezone.utc).date()}. "
                f"Final verdict: {result.verdict}. "
                f"Bull score: {result.bull_score}/5. Bear score: {result.bear_score}/5. "
                f"Agent stances: {agent_summary}. "
                f"Summary: {result.moderator_summary}"
            )
            entry_id = f"debate_{result.ticker}_{int(time.time())}"
            col.add(
                documents=[doc],
                metadatas=[{
                    "ticker": result.ticker,
                    "verdict": result.verdict,
                    "bull_score": result.bull_score,
                    "bear_score": result.bear_score,
                    "date": str(datetime.now(timezone.utc).date()),
                }],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_debate failed: {e}")

    def add_swarm_reflection(self, ticker: str, signal: int, verdict: str,
                              confidence: float, price_change_pct: float,
                              lesson: str, regime: str, correct: bool) -> None:
        """Store a daily-outcome reflection so future swarm runs learn from results."""
        col = self._safe_col("swarm_reflections")
        if not col:
            return
        try:
            outcome = "correct" if correct else "incorrect"
            effectiveness = 0.7 if correct else 0.3
            doc = (
                f"Swarm reflection for {ticker}: signal was {signal} ({verdict}), "
                f"confidence {confidence:.2f}. Next-day price moved {price_change_pct:+.1f}%. "
                f"Outcome: {outcome}. Regime: {regime}. Lesson: {lesson}"
            )
            entry_id = f"swarm_ref_{ticker}_{int(time.time())}"
            col.add(
                documents=[doc],
                metadatas=[{
                    "ticker": ticker,
                    "signal": signal,
                    "verdict": verdict,
                    "confidence": confidence,
                    "price_change_pct": price_change_pct,
                    "outcome": outcome,
                    "regime": regime,
                    "effectiveness_score": effectiveness,
                    "date": str(datetime.now(timezone.utc).date()),
                }],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_swarm_reflection failed: {e}")

    def query_swarm_reflections(self, query_text: str, n_results: int = 3, filters: Optional[dict] = None) -> list[str]:
        """Retrieve swarm reflections ranked by effectiveness and recency."""
        col = self._safe_col("swarm_reflections")
        if not col:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            actual_n = min(n_results, count)
            oversample = min(actual_n * 3, count)
            rows = col.query(query_texts=[query_text], n_results=oversample, where=filters)
            docs = rows.get("documents", [[]])[0]
            metas = rows.get("metadatas", [[]])[0]
            ranked = sorted(
                zip(docs, metas),
                key=lambda x: (
                    float(x[1].get("effectiveness_score", 0.5)),
                    x[1].get("date", ""),
                ),
                reverse=True,
            )
            return [d for d, _ in ranked[:actual_n]]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_swarm_reflections failed: {e}")
            return []

    def query_stock_profile(self, ticker: str) -> str:
        """Retrieve the 15-year stock profile for a ticker (data lake)."""
        col = self._safe_col("stock_profiles")
        if not col:
            return ""
        try:
            count = col.count()
            if count == 0:
                return ""
            results = col.query(query_texts=[f"{ticker} 15-year profile"], n_results=1,
                                where={"ticker": ticker})
            docs = results.get("documents", [[]])[0]
            return docs[0] if docs else ""
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_stock_profile failed: {e}")
            return ""

    def query_earnings_memory(self, ticker: str, query_text: Optional[str] = None,
                              n_results: int = 5) -> list[str]:
        """Retrieve earnings-event memories for a ticker (data lake)."""
        col = self._safe_col("earnings_memory")
        if not col:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            q = query_text or f"{ticker} earnings surprise reaction"
            actual_n = min(n_results, count)
            results = col.query(query_texts=[q], n_results=actual_n,
                                where={"ticker": ticker})
            return results.get("documents", [[]])[0]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_earnings_memory failed: {e}")
            return []

    def add_macro_alert(self, alert) -> None:
        """Store a MacroAlert from the news scanner."""
        col = self._safe_col("macro_alerts")
        if not col:
            return
        try:
            doc = (
                f"[{alert.source}] {alert.title}: {alert.summary} "
                f"Urgency: {alert.urgency}/10. "
                f"Affected sectors: {', '.join(alert.affected_sectors)}."
            )
            col.add(
                documents=[doc],
                metadatas=[{
                    "source": alert.source,
                    "urgency": alert.urgency,
                    "sectors": json.dumps(alert.affected_sectors),
                    "date": str(datetime.now(timezone.utc).date()),
                }],
                ids=[alert.id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_macro_alert failed (likely duplicate): {e}")

    def add_backtest(self, result) -> None:
        """Store a BacktestResult after /backtest."""
        col = self._safe_col("strategy_backtests")
        if not col:
            return
        try:
            buy_desc  = "; ".join(f"{f.metric} {f.op} {f.value}" for f in result.strategy.filters)
            sell_desc = "; ".join(f"{f.metric} {f.op} {f.value}" for f in (result.strategy.sell_filters or []))
            doc = (
                f"Backtest: {result.strategy.name}. "
                f"Period: {result.strategy.start_date} to {result.strategy.end_date}. "
                f"Universe: {len(result.strategy.universe)} stocks. "
                f"Buy when: {buy_desc}. "
                + (f"Sell when: {sell_desc}. " if sell_desc else "")
                + f"$10,000 → ${result.final_value:,.0f} ({result.total_return_pct:+.1f}%). "
                f"CAGR: {result.cagr:.1f}% vs SPY {result.benchmark_cagr:.1f}%. "
                f"Sharpe: {result.sharpe_ratio:.2f}. Max Drawdown: {result.max_drawdown:.1f}%. "
                f"Win Rate: {result.win_rate:.1f}%. Total trades: {result.total_trades}. "
                f"Explanation: {result.ai_explanation[:400]}"
            )
            entry_id = f"backtest_{int(time.time())}"
            col.add(
                documents=[doc],
                metadatas=[{
                    "strategy_name":   result.strategy.name,
                    "strategy_type":   result.strategy.strategy_type,
                    "strategy_category": getattr(result.strategy, "strategy_category", "custom"),
                    "preset_id":       getattr(result.strategy, "preset_id", None) or "",
                    "market_regime":   getattr(result.reflection, "market_regime", "unknown"),
                    "drawdown_bucket": getattr(result.reflection, "drawdown_bucket", "unknown"),
                    "start_date":      result.strategy.start_date,
                    "end_date":        result.strategy.end_date,
                    "cagr":            result.cagr,
                    "sharpe":          result.sharpe_ratio,
                    "win_rate":        result.win_rate,
                    "max_drawdown":    result.max_drawdown,
                    "total_return_pct": result.total_return_pct,
                    "final_value":     result.final_value,
                    "total_trades":    result.total_trades,
                    "outperformed":    str(result.outperformed),
                    "date":            str(datetime.now(timezone.utc).date()),
                }],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_backtest failed: {e}")

    def add_reflection(self, result) -> None:
        """Store compact lesson-learned reflection with effectiveness score for retrieval."""
        col = self._safe_col("strategy_reflections")
        if not col:
            return
        try:
            reflection = result.reflection
            strategy = result.strategy
            effectiveness = getattr(reflection, "effectiveness_score", 0.5)
            is_failure = reflection.outcome in (
                "severe_failure", "capital_destruction", "low_quality_underperformance",
            )
            label = "FAILURE POST-MORTEM" if is_failure else "Lesson learned"
            doc = (
                f"{label} from strategy '{strategy.name}' ({strategy.strategy_type}). "
                f"Hypothesis: {reflection.hypothesis} "
                f"Outcome: {reflection.outcome}. "
                f"Regime: {reflection.market_regime}. "
                f"Drawdown bucket: {reflection.drawdown_bucket}. "
                f"Adjustment: {reflection.adjustment}. "
                f"CAGR {result.cagr:.1f}% vs benchmark {result.benchmark_cagr:.1f}%. "
                f"Win rate {result.win_rate:.1f}%."
            )
            entry_id = f"reflection_{int(time.time())}"
            col.add(
                documents=[doc],
                metadatas=[{
                    "strategy_name": strategy.name,
                    "strategy_type": strategy.strategy_type,
                    "market_regime": reflection.market_regime,
                    "drawdown_bucket": reflection.drawdown_bucket,
                    "outcome": reflection.outcome,
                    "is_failure": str(is_failure),
                    "confidence": reflection.confidence,
                    "effectiveness_score": effectiveness,
                    "date": str(datetime.now(timezone.utc).date()),
                }],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_reflection failed: {e}")

    def update_reflection_effectiveness(self, reflection_ids: list[str], new_backtest_outperformed: bool) -> None:
        """
        Memory voting: update effectiveness scores of reflections that were
        retrieved and used to inform a new backtest.  If the new backtest
        outperformed, boost the retrieved reflections; otherwise decay them.
        """
        col = self._safe_col("strategy_reflections")
        if not col or not reflection_ids:
            return
        try:
            rows = col.get(include=["metadatas"])
            all_ids = rows.get("ids", [])
            all_metas = rows.get("metadatas", [])
            id_to_meta = dict(zip(all_ids, all_metas))

            boost = 0.05 if new_backtest_outperformed else -0.03
            for rid in reflection_ids:
                meta = id_to_meta.get(rid)
                if not meta:
                    continue
                old_score = float(meta.get("effectiveness_score", 0.5))
                new_score = max(0.0, min(1.0, old_score + boost))
                if hasattr(self._vector_backend, "update_metadata"):
                    self._vector_backend.update_metadata(
                        "strategy_reflections", rid, {"effectiveness_score": new_score}
                    )
            logger.info(
                "[KnowledgeStore] updated effectiveness for %d reflections (outperformed=%s)",
                len(reflection_ids), new_backtest_outperformed,
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] update_reflection_effectiveness failed: {e}")

    def add_price_movement(self, ticker: str, change_pct: float, volume_ratio: float, sector: str, context: str) -> None:
        col = self._safe_col("price_movements")
        if not col:
            return
        try:
            today = str(datetime.now(timezone.utc).date())
            direction = "rose" if change_pct >= 0 else "fell"
            doc = (
                f"{ticker} {direction} {abs(change_pct):.1f}% on {today}. "
                f"Volume {volume_ratio:.1f}x average. Sector: {sector}. {context}"
            )
            col.add(
                documents=[doc],
                metadatas=[{"ticker": ticker, "change_pct": change_pct, "sector": sector, "date": today}],
                ids=[f"pm_{ticker}_{today}_{int(time.time())}"],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_price_movement failed: {e}")

    def add_macro_snapshot(self, snapshot: dict) -> None:
        col = self._safe_col("macro_snapshots")
        if not col:
            return
        try:
            today = str(datetime.now(timezone.utc).date())
            nar = snapshot.get("macro_narrative") or ""
            doc = (
                f"Macro snapshot {today}. "
                f"{nar} "
                f"Fed Funds {snapshot.get('fed_funds_rate', 'N/A')}%, "
                f"CPI YoY {snapshot.get('cpi_yoy', 'N/A')}%, "
                f"2Y/10Y Treasury {snapshot.get('treasury_2y', 'N/A')}% / {snapshot.get('treasury_10y', 'N/A')}%, "
                f"USD broad index {snapshot.get('usd_broad_index', 'N/A')} "
                f"({snapshot.get('usd_strength_label', 'n/a')} vs 5d), "
                f"ICE DXY {snapshot.get('dxy_level', 'N/A')} "
                f"({snapshot.get('dxy_strength_label', 'n/a')} vs 5d), "
                f"Unemployment {snapshot.get('unemployment', 'N/A')}%."
            )
            meta = {"date": today}
            for k in (
                "fed_funds_rate",
                "cpi_yoy",
                "treasury_10y",
                "treasury_2y",
                "usd_broad_index",
                "usd_strength_label",
                "dxy_level",
                "dxy_change_5d_pct",
                "dxy_strength_label",
            ):
                v = snapshot.get(k)
                if v is not None:
                    meta[k] = v
            col.add(
                documents=[doc],
                metadatas=[meta],
                ids=[f"macro_{today}"],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_macro_snapshot failed: {e}")

    def add_youtube_insight(self, channel: str, title: str, description: str, published: str, tags: list) -> None:
        col = self._safe_col("youtube_insights")
        if not col:
            return
        try:
            today = str(datetime.now(timezone.utc).date())
            tags_str = ", ".join(tags[:8]) if tags else ""
            doc = (
                f"[{channel} {published}] '{title}' — {description[:300]}. "
                f"Tags: {tags_str}"
            )
            entry_id = f"yt_{channel}_{int(time.time())}"
            col.add(
                documents=[doc],
                metadatas=[{"channel": channel, "title": title, "published": published, "date": today}],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_youtube_insight failed: {e}")

    def add_chat_memory(
        self, user_id: str, session_id: str,
        summary: str, tickers: list, topic: str,
    ) -> None:
        """Store a salient chat exchange as searchable memory (per-user)."""
        col = self._safe_col("chat_memories")
        if not col:
            return
        try:
            from .agent_policy_guardrails import redact_secrets_in_text
            safe_summary = redact_secrets_in_text(summary)
            today = str(datetime.now(timezone.utc).date())
            col.add(
                documents=[safe_summary],
                metadatas=[{
                    "user_id": user_id,
                    "session_id": session_id,
                    "tickers": json.dumps(tickers),
                    "topic": topic,
                    "date": today,
                }],
                ids=[f"chat_mem_{session_id}_{int(time.time())}"],
            )
            logger.info(
                "[KnowledgeStore] chat_memory stored for user=%s tickers=%s",
                user_id[:8], tickers,
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_chat_memory failed: {e}")

    def query_chat_memories(
        self, user_id: str, query_text: str, n_results: int = 5,
    ) -> list[str]:
        """Retrieve past chat memories for a user (semantic search)."""
        col = self._safe_col("chat_memories")
        if not col:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            actual_n = min(n_results, count)
            results = col.query(
                query_texts=[query_text],
                n_results=actual_n,
                where={"user_id": user_id},
            )
            return results.get("documents", [[]])[0]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_chat_memories failed: {e}")
            return []

    # ── QUERY METHODS ─────────────────────────────────────────────────────────

    def get_strategy_leaderboard(self, n: int = 20) -> list:
        """Return top N backtested strategies sorted by CAGR (best first)."""
        col = self._safe_col("strategy_backtests")
        if not col or col.count() == 0:
            return []
        try:
            count = col.count()
            if count > 1000:
                logger.warning(
                    "[KnowledgeStore] strategy_backtests has %d entries; "
                    "get_strategy_leaderboard loads all into memory. "
                    "Consider pagination or database-side sorting.", count
                )
            results = col.get(include=["documents", "metadatas"])
            ids = results.get("ids", [])
            entries = []
            for i, (doc, meta) in enumerate(zip(
                results.get("documents", []),
                results.get("metadatas", []),
            )):
                entries.append({
                    "id":               ids[i] if i < len(ids) else f"entry_{i}",
                    "strategy_name":    meta.get("strategy_name", "Unknown"),
                    "strategy_type":    meta.get("strategy_type", "unknown"),
                    "strategy_category": meta.get("strategy_category", "custom"),
                    "preset_id":        meta.get("preset_id", ""),
                    "start_date":       meta.get("start_date", ""),
                    "end_date":         meta.get("end_date", ""),
                    "cagr":             float(meta.get("cagr", 0.0)),
                    "sharpe":           float(meta.get("sharpe", 0.0)),
                    "win_rate":         float(meta.get("win_rate", 0.0)),
                    "max_drawdown":     float(meta.get("max_drawdown", 0.0)),
                    "total_return_pct": float(meta.get("total_return_pct", 0.0)),
                    "final_value":      float(meta.get("final_value", 0.0)),
                    "total_trades":     int(meta.get("total_trades", 0)),
                    "outperformed":     meta.get("outperformed") == "True",
                    "date_run":         meta.get("date", ""),
                    "summary":          (doc or "")[:250],
                })
            entries.sort(key=lambda x: x["cagr"], reverse=True)
            return entries[:n]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] get_strategy_leaderboard failed: {e}")
            return []

    def query(self, collection: str, query_text: str, n_results: int = 3) -> list[str]:
        """Semantic similarity search. Returns list of document strings."""
        try:
            from .telemetry import get_tracer

            tracer = get_tracer()
            with tracer.start_as_current_span("rag.query") as span:
                try:
                    span.set_attribute("rag.collection", collection)
                except Exception:
                    pass
                return self._query_impl(collection, query_text, n_results)
        except Exception:
            return self._query_impl(collection, query_text, n_results)

    def _query_impl(self, collection: str, query_text: str, n_results: int) -> list[str]:
        col = self._safe_col(collection)
        if not col:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            actual_n = min(n_results, count)
            results = col.query(query_texts=[query_text], n_results=actual_n)
            return results.get("documents", [[]])[0]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query({collection}) failed: {e}")
            return []

    def query_with_metadata(
        self, collection: str, query_text: str, n_results: int = 8
    ) -> list[dict]:
        """
        Semantic search with documents, metadata, and distance per hit (for chat RAG reranking).
        Each item: {"document": str, "metadata": dict, "distance": float}.
        """
        col = self._safe_col(collection)
        if not col:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            actual_n = min(n_results, count)
            results = col.query(query_texts=[query_text], n_results=actual_n)
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]
            out = []
            for i, doc in enumerate(docs):
                dval = 1.0
                if dists is not None and i < len(dists):
                    try:
                        dval = float(dists[i])
                    except Exception:
                        dval = 1.0
                out.append(
                    {
                        "document": doc or "",
                        "metadata": metas[i] if i < len(metas) else {},
                        "distance": dval,
                    }
                )
            return out
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_with_metadata({collection}) failed: {e}")
            return []

    def query_reflections(self, query_text: str, n_results: int = 5, filters: Optional[dict] = None):
        """Reflection retrieval with metadata filtering, effectiveness weighting, and recency."""
        col = self._safe_col("strategy_reflections")
        if not col:
            return [], [], {"retrieved_docs_count": 0, "reflection_hits": 0, "retrieved_reflection_ids": []}
        try:
            count = col.count()
            if count == 0:
                return [], [], {"retrieved_docs_count": 0, "reflection_hits": 0, "retrieved_reflection_ids": []}

            top_k = max(1, min(n_results or self._retrieval_default_top_k, self._retrieval_max_top_k, count))
            oversample = min(max(top_k * 3, top_k), count)
            where = filters if filters else None
            rows = col.query(query_texts=[query_text], n_results=oversample, where=where)
            docs = rows.get("documents", [[]])[0]
            metas = rows.get("metadatas", [[]])[0]
            ids = rows.get("ids", [[]])[0]

            ranked = list(zip(docs, metas, ids))
            ranked.sort(
                key=lambda x: (
                    float(x[1].get("effectiveness_score", 0.5)),
                    x[1].get("date", ""),
                ),
                reverse=True,
            )
            ranked = ranked[:top_k]
            out_docs = [d for d, _, _ in ranked]
            out_meta = [m for _, m, _ in ranked]
            out_ids = [i for _, _, i in ranked]
            telemetry = {
                "retrieved_docs_count": len(out_docs),
                "reflection_hits": len(out_meta),
                "retrieved_reflection_ids": out_ids,
            }
            return out_docs, out_meta, telemetry
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_reflections failed: {e}")
            return [], [], {"retrieved_docs_count": 0, "reflection_hits": 0, "retrieved_reflection_ids": []}

    def get_recent_reflections(self, n: int = 20) -> list[dict]:
        """Return recent reflection entries for debugging and observability."""
        col = self._safe_col("strategy_reflections")
        if not col:
            return []
        try:
            rows = col.get(include=["documents", "metadatas", "ids"])
            docs = rows.get("documents", []) or []
            metas = rows.get("metadatas", []) or []
            ids = rows.get("ids", []) or []

            entries = []
            for i, (doc, meta) in enumerate(zip(docs, metas)):
                entries.append({
                    "id": ids[i] if i < len(ids) else f"reflection_{i}",
                    "document": doc,
                    "metadata": meta or {},
                })
            entries.sort(key=lambda x: x["metadata"].get("date", ""), reverse=True)
            return entries[: max(1, n)]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] get_recent_reflections failed: {e}")
            return []

    def format_context(self, docs: list[str]) -> str:
        """Format a list of retrieved docs into a prompt-ready string."""
        if not docs:
            return "No relevant historical context found."
        return "\n".join(f"[Prior analysis {i+1}]: {d}" for i, d in enumerate(docs))

    # ── S&P 500 INGESTION WRITE/QUERY METHODS ─────────────────────────────────

    def upsert_sp500_fundamental(self, ticker: str, sector: str, narrative: str,
                                  pe_ratio: float = 0.0, eps: float = 0.0,
                                  market_cap_b: float = 0.0) -> None:
        """Upsert a natural-language fundamental narrative for one S&P 500 ticker."""
        col = self._safe_col("sp500_fundamentals_narratives")
        if not col:
            return
        try:
            today = str(datetime.now(timezone.utc).date())
            # Use ticker+date as ID so each day's snapshot replaces the previous
            entry_id = f"sp500_fund_{ticker}_{today}"
            col.add(
                documents=[narrative],
                metadatas=[{
                    "ticker": ticker,
                    "sector": sector,
                    "pe_ratio": pe_ratio,
                    "eps": eps,
                    "market_cap_b": market_cap_b,
                    "date": today,
                }],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] upsert_sp500_fundamental({ticker}) failed: {e}")

    def query_sp500_fundamentals(self, query_text: str, n_results: int = 5,
                                  sector: Optional[str] = None) -> list[str]:
        """Semantic search over S&P 500 fundamental narratives. Optionally filter by sector."""
        col = self._safe_col("sp500_fundamentals_narratives")
        if not col:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            where = {"sector": sector} if sector else None
            actual_n = min(n_results, count)
            results = col.query(query_texts=[query_text], n_results=actual_n, where=where)
            return results.get("documents", [[]])[0]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_sp500_fundamentals failed: {e}")
            return []

    def upsert_sp500_sector_analysis(self, sector_name: str, etf_ticker: str,
                                      narrative: str, week_return_pct: float = 0.0) -> None:
        """Upsert a sector rotation/momentum narrative for one S&P 500 sector."""
        col = self._safe_col("sp500_sector_analysis")
        if not col:
            return
        try:
            today = str(datetime.now(timezone.utc).date())
            entry_id = f"sp500_sector_{etf_ticker}_{today}"
            col.add(
                documents=[narrative],
                metadatas=[{
                    "sector": sector_name,
                    "etf_ticker": etf_ticker,
                    "week_return_pct": week_return_pct,
                    "date": today,
                }],
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] upsert_sp500_sector_analysis({sector_name}) failed: {e}")

    def query_sp500_sector_analysis(self, query_text: str, n_results: int = 5) -> list[str]:
        """Semantic search over S&P 500 sector rotation narratives."""
        col = self._safe_col("sp500_sector_analysis")
        if not col:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            actual_n = min(n_results, count)
            results = col.query(query_texts=[query_text], n_results=actual_n)
            return results.get("documents", [[]])[0]
        except Exception as e:
            logger.warning(f"[KnowledgeStore] query_sp500_sector_analysis failed: {e}")
            return []

    # ── STATS & EXPORT ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return count of entries per collection."""
        sizes = {}
        for name, col in self._cols.items():
            try:
                sizes[name] = col.count()
            except Exception:
                sizes[name] = 0
        return {
            "collections": sizes,
            "total_entries": sum(sizes.values()),
            "vector_backend": self._active_vector_backend,
            "pipeline_status": self._pipeline_status,
        }

    def export_jsonl(self) -> str:
        """
        Export debate_history and strategy_backtests as JSONL fine-tuning pairs.
        Format: OpenAI-compatible {"messages": [...]} per line.
        """
        lines = []

        # Debate history training pairs
        col = self._safe_col("debate_history")
        if col and col.count() > 0:
            results = col.get(include=["documents", "metadatas"])
            for doc, meta in zip(results.get("documents", []), results.get("metadatas", [])):
                ticker = meta.get("ticker", "UNKNOWN")
                verdict = meta.get("verdict", "NEUTRAL")
                pair = {
                    "messages": [
                        {"role": "system", "content": "You are a finance investment analyst specialising in equity research."},
                        {"role": "user", "content": f"Analyse {ticker} investment debate."},
                        {"role": "assistant", "content": doc},
                    ]
                }
                lines.append(json.dumps(pair))

        # Backtest history training pairs
        col = self._safe_col("strategy_backtests")
        if col and col.count() > 0:
            results = col.get(include=["documents", "metadatas"])
            for doc, meta in zip(results.get("documents", []), results.get("metadatas", [])):
                strategy_name = meta.get("strategy_name", "Custom Strategy")
                pair = {
                    "messages": [
                        {"role": "system", "content": "You are a quantitative finance expert specialising in strategy backtesting."},
                        {"role": "user", "content": f"Explain the results of the '{strategy_name}' backtesting strategy."},
                        {"role": "assistant", "content": doc},
                    ]
                }
                lines.append(json.dumps(pair))

        return "\n".join(lines)

    def update_pipeline_status(self, **kwargs) -> None:
        self._pipeline_status.update(kwargs)
        self._pipeline_status["total_collection_sizes"] = {
            name: col.count() for name, col in self._cols.items()
        }


# Module-level singleton
_store: Optional[KnowledgeStore] = None

def get_knowledge_store() -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KnowledgeStore()
    return _store
