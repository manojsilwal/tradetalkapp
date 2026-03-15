"""
Knowledge Store — ChromaDB singleton with 7 collections.
Every agent run, debate, backtest, and daily pipeline job writes here.
Agents query this store before every LLM call (RAG).
All data is cumulative — semantic search spans the full history.
"""
import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Persist to disk so knowledge survives restarts
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./chroma_db")

COLLECTIONS = [
    "swarm_history",       # every /trace call
    "debate_history",      # every /debate call
    "macro_alerts",        # every 60s news scan (already exists logic)
    "strategy_backtests",  # every /backtest call
    "price_movements",     # daily pipeline — top movers
    "macro_snapshots",     # daily pipeline — FRED indicators
    "youtube_insights",    # daily pipeline — finance channel videos
]


class KnowledgeStore:
    """
    Singleton ChromaDB wrapper.
    Uses PersistentClient so all knowledge survives server restarts.
    """

    def __init__(self):
        self._client = None
        self._cols: dict = {}
        self._pipeline_status: dict = {
            "last_run": None,
            "price_movements_added": 0,
            "youtube_videos_added": 0,
            "macro_snapshot_added": False,
            "total_collection_sizes": {},
        }
        self._init_chroma()

    def _init_chroma(self):
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=CHROMA_PATH)
            for name in COLLECTIONS:
                self._cols[name] = self._client.get_or_create_collection(name=name)
            logger.info(f"[KnowledgeStore] ChromaDB ready at {CHROMA_PATH} — {len(COLLECTIONS)} collections")
        except Exception as e:
            logger.error(f"[KnowledgeStore] ChromaDB init failed: {e}")

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
                f"Explanation: {result.gemini_explanation[:400]}"
            )
            entry_id = f"backtest_{int(time.time())}"
            col.add(
                documents=[doc],
                metadatas=[{
                    "strategy_name":   result.strategy.name,
                    "strategy_type":   result.strategy.strategy_type,
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
                metadatas={"ticker": ticker, "change_pct": change_pct, "sector": sector, "date": today},
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
            doc = (
                f"Macro snapshot {today}: "
                f"Fed Funds Rate {snapshot.get('fed_funds_rate', 'N/A')}%, "
                f"CPI YoY {snapshot.get('cpi_yoy', 'N/A')}%, "
                f"10Y Treasury {snapshot.get('treasury_10y', 'N/A')}%, "
                f"Unemployment {snapshot.get('unemployment', 'N/A')}%."
            )
            col.add(
                documents=[doc],
                metadatas={**snapshot, "date": today},
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
                metadatas={"channel": channel, "title": title, "published": published, "date": today},
                ids=[entry_id],
            )
        except Exception as e:
            logger.warning(f"[KnowledgeStore] add_youtube_insight failed: {e}")

    # ── QUERY METHODS ─────────────────────────────────────────────────────────

    def get_strategy_leaderboard(self, n: int = 20) -> list:
        """Return top N backtested strategies sorted by CAGR (best first)."""
        col = self._safe_col("strategy_backtests")
        if not col or col.count() == 0:
            return []
        try:
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

    def format_context(self, docs: list[str]) -> str:
        """Format a list of retrieved docs into a prompt-ready string."""
        if not docs:
            return "No relevant historical context found."
        return "\n".join(f"[Prior analysis {i+1}]: {d}" for i, d in enumerate(docs))

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
