"""
Integration tests for the three Phase-2 producers:

* ``AgentPair.run``           -> decision_type == "swarm_factor"
* ``_run_full_debate_impl``   -> decision_type == "debate"
* ``routers/chat.py chat_send_message`` stream end -> decision_type == "chat_turn"

Each test injects mock collaborators so we never hit the network and never
touch the real progress.db. The ledger is backed by a per-test temp SQLite
file so we can assert on what landed in ``decision_events``.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("GEMINI_PRIMARY", "0")
os.environ.setdefault("GEMINI_LLM_FALLBACK", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend.schemas import (  # noqa: E402
    MarketState,
    MarketRegime,
    VerificationStatus,
)
from backend.agents import AgentPair  # noqa: E402


class _LedgerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "d.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        dl._reset_singleton_for_tests()

    def tearDown(self) -> None:
        dl._reset_singleton_for_tests()
        os.environ.pop("DECISIONS_DB_PATH", None)


# ── AgentPair producer ──────────────────────────────────────────────────────


class _MiniPair(AgentPair):
    """Minimal AgentPair subclass — one-shot VERIFIED signal, no network."""

    def __init__(self, signal: int, confidence: float = 0.77) -> None:
        super().__init__(factor_name="Test Factor", max_iterations=1)
        self._signal = signal
        self._confidence = confidence

    async def _analyst_step(self, market_state, ticker, history):
        return {"rationale": f"[{ticker}] signal={self._signal}", "trading_signal": self._signal}

    async def _qa_verifier_step(self, analyst_report, market_state, history):
        return {
            "status": VerificationStatus.VERIFIED,
            "rationale": "ok",
            "confidence": self._confidence,
        }


class TestAgentPairEmitsFactorDecision(_LedgerTestBase):
    def test_verified_factor_lands_in_ledger(self) -> None:
        ms = MarketState(
            credit_stress_index=1.3,
            k_shape_spending_divergence=0.2,
            market_regime=MarketRegime.BULL_NORMAL,
        )
        pair = _MiniPair(signal=1, confidence=0.82)
        result = asyncio.run(pair.run(ms, ticker="MSFT"))

        self.assertEqual(result.trading_signal, 1)
        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="swarm_factor")
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d.symbol, "MSFT")
        self.assertEqual(d.verdict, "BUY")
        self.assertAlmostEqual(d.confidence, 0.82, places=4)
        self.assertIn("Test Factor", d.output.get("factor_name", ""))
        # source_route is stamped with the concrete subclass name for traceability
        self.assertIn("_MiniPair", d.source_route)
        self.assertIn("backend/agents.py", d.source_route)

    def test_rejected_factor_still_emits(self) -> None:
        class _FailPair(_MiniPair):
            async def _qa_verifier_step(self, analyst_report, market_state, history):
                return {
                    "status": VerificationStatus.REJECTED,
                    "rationale": "macro block",
                    "confidence": 0.9,
                }

        ms = MarketState(market_regime=MarketRegime.BULL_NORMAL)
        pair = _FailPair(signal=1)
        asyncio.run(pair.run(ms, ticker="tsla"))
        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="swarm_factor")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].symbol, "TSLA")
        # Rejected pairs force trading_signal=0 -> verdict NEUTRAL
        self.assertEqual(decisions[0].verdict, "NEUTRAL")

    def test_features_capture_macro_regime(self) -> None:
        ms = MarketState(
            credit_stress_index=1.9,
            k_shape_spending_divergence=-0.1,
            market_regime=MarketRegime.BULL_NORMAL,
        )
        pair = _MiniPair(signal=-1)
        asyncio.run(pair.run(ms, ticker="NVDA"))
        # Inspect feature_snapshots directly.
        backend = dl.get_ledger()
        conn = backend._conn()  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT feature_name, value_num, value_str, regime "
            "FROM feature_snapshots ORDER BY feature_name"
        ).fetchall()
        names = {r["feature_name"] for r in rows}
        self.assertIn("credit_stress_index", names)
        self.assertIn("market_regime", names)
        csi_row = next(r for r in rows if r["feature_name"] == "credit_stress_index")
        self.assertAlmostEqual(csi_row["value_num"], 1.9, places=4)
        self.assertEqual(csi_row["regime"], MarketRegime.BULL_NORMAL.value)


# ── Debate producer ────────────────────────────────────────────────────────


class TestDebateEmitsDecision(_LedgerTestBase):
    def test_full_debate_emits_debate_decision(self) -> None:
        from backend import debate_agents as da
        from backend.schemas import AgentStance, DebateArgument

        # 5 fake arguments spread across stances
        def _arg(role, stance):
            return DebateArgument(
                agent_role=role,
                agent_icon="•",
                stance=stance,
                headline=f"{role} headline",
                key_points=["p1", "p2"],
                confidence=0.6,
            )

        arguments = [
            _arg("bull", AgentStance.BULLISH),
            _arg("bear", AgentStance.BEARISH),
            _arg("macro", AgentStance.NEUTRAL),
            _arg("value", AgentStance.NEUTRAL),
            _arg("momentum", AgentStance.NEUTRAL),
        ]

        async def _fake_agent(*args, **kwargs):
            role = args[0] if args else kwargs.get("role")
            # The individual agent runners are bound by name -- we monkey-patch
            # the symbol-level functions to return our canned arguments instead.
            return arguments.pop(0)

        # Monkey-patch the 5 agent runners on the debate_agents module.
        async def _bull(*_a, **_kw): return _arg("bull", AgentStance.BULLISH)
        async def _bear(*_a, **_kw): return _arg("bear", AgentStance.BEARISH)
        async def _macro(*_a, **_kw): return _arg("macro", AgentStance.NEUTRAL)
        async def _value(*_a, **_kw): return _arg("value", AgentStance.NEUTRAL)
        async def _momentum(*_a, **_kw): return _arg("momentum", AgentStance.NEUTRAL)

        original = {
            "run_bull_agent": da.run_bull_agent,
            "run_bear_agent": da.run_bear_agent,
            "run_macro_agent": da.run_macro_agent,
            "run_value_agent": da.run_value_agent,
            "run_momentum_agent": da.run_momentum_agent,
            "run_moderator": da.run_moderator,
            "_store_agent_snapshot": da._store_agent_snapshot,
        }
        da.run_bull_agent = _bull          # type: ignore[assignment]
        da.run_bear_agent = _bear          # type: ignore[assignment]
        da.run_macro_agent = _macro        # type: ignore[assignment]
        da.run_value_agent = _value        # type: ignore[assignment]
        da.run_momentum_agent = _momentum  # type: ignore[assignment]

        async def _fake_moderator(ticker, args, ks, llm, **_kw):
            return "BUY", 0.74, "synth", None

        da.run_moderator = _fake_moderator  # type: ignore[assignment]
        da._store_agent_snapshot = lambda *a, **kw: None  # type: ignore[assignment]

        try:
            # Minimal ks + llm. ks isn't queried inside _run_full_debate_impl
            # directly here because we stubbed all 5 agent runners.
            ks = SimpleNamespace()
            llm = SimpleNamespace()
            macro_state = {"market_regime": "BULL_NORMAL", "credit_stress_index": 1.2}
            result = asyncio.run(
                da._run_full_debate_impl(
                    "aapl", {"ticker": "AAPL"}, macro_state, ks, llm,
                )
            )
        finally:
            da.run_bull_agent = original["run_bull_agent"]          # type: ignore[assignment]
            da.run_bear_agent = original["run_bear_agent"]          # type: ignore[assignment]
            da.run_macro_agent = original["run_macro_agent"]        # type: ignore[assignment]
            da.run_value_agent = original["run_value_agent"]        # type: ignore[assignment]
            da.run_momentum_agent = original["run_momentum_agent"]  # type: ignore[assignment]
            da.run_moderator = original["run_moderator"]            # type: ignore[assignment]
            da._store_agent_snapshot = original["_store_agent_snapshot"]  # type: ignore[assignment]

        self.assertEqual(result.verdict, "BUY")
        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="debate")
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d.symbol, "AAPL")
        self.assertEqual(d.verdict, "BUY")
        self.assertEqual(d.horizon_hint, "5d")
        self.assertAlmostEqual(d.confidence, 0.74, places=4)
        self.assertEqual(len(d.output.get("arguments", [])), 5)
        self.assertEqual(d.output["bull_score"], 1)
        self.assertEqual(d.output["bear_score"], 1)
        self.assertEqual(d.output["neutral_score"], 3)

        # regime should be captured as a feature
        backend = dl.get_ledger()
        conn = backend._conn()  # type: ignore[attr-defined]
        names = {
            r["feature_name"]
            for r in conn.execute(
                "SELECT feature_name FROM feature_snapshots WHERE decision_id = ?",
                (d.decision_id,),
            ).fetchall()
        }
        self.assertIn("market_regime", names)
        self.assertIn("credit_stress_index", names)
        self.assertIn("bull_score", names)


# ── Chat producer ──────────────────────────────────────────────────────────


class TestChatTurnEmitsDecision(_LedgerTestBase):
    def test_emit_after_evidence_contract(self) -> None:
        """
        Directly exercise the snippet from ``routers/chat.py`` that converts
        a tool_trace + evidence into a ledger decision. We replicate the
        surrounding state without spinning up FastAPI so the test stays fast.
        """
        tool_trace = [
            {"name": "get_stock_quote", "outcome": "success"},
            {"name": "get_top_movers", "outcome": "empty"},
        ]
        evidence = {
            "confidence_band": "high",
            "abstain_reason": None,
            "tools_called": ["get_stock_quote", "get_top_movers"],
        }
        sticky = {"active_ticker": "AMZN"}

        refs = []
        for idx, t in enumerate(tool_trace):
            nm = t.get("name") or ""
            if nm and t.get("outcome") == "success":
                refs.append(
                    dl.EvidenceRef(
                        chunk_id=f"tool:{nm}",
                        collection="chat_tool_trace",
                        rank=idx,
                    )
                )
        feats = [
            dl.FeatureValue(name="confidence_band", value_str=evidence["confidence_band"]),
            dl.FeatureValue(name="abstain_reason", value_str=evidence["abstain_reason"] or ""),
            dl.FeatureValue(name="n_tools", value_num=float(len(tool_trace))),
        ]
        did = dl.emit_decision(
            decision_type="chat_turn",
            user_id="user-1",
            symbol=sticky["active_ticker"],
            horizon_hint="none",
            output={
                "session_id": "s1",
                "user_message": "what's AMZN doing?",
                "assistant_text": "AMZN is up 1.2%",
                "evidence": evidence,
            },
            source_route="backend/routers/chat.py::chat_send_message",
            evidence=refs,
            features=feats,
        )
        # Assertions on the landed state
        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="chat_turn")
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d.decision_id, did)
        self.assertEqual(d.user_id, "user-1")
        self.assertEqual(d.symbol, "AMZN")
        self.assertEqual(d.output["evidence"]["confidence_band"], "high")

        # Evidence rows: one per successful tool
        backend = dl.get_ledger()
        conn = backend._conn()  # type: ignore[attr-defined]
        evidence_rows = conn.execute(
            "SELECT chunk_id, collection, rank FROM decision_evidence "
            "WHERE decision_id = ? ORDER BY rank",
            (did,),
        ).fetchall()
        self.assertEqual(len(evidence_rows), 1)
        self.assertEqual(evidence_rows[0]["chunk_id"], "tool:get_stock_quote")
        self.assertEqual(evidence_rows[0]["collection"], "chat_tool_trace")

        # Feature rows
        feats_rows = {
            r["feature_name"]
            for r in conn.execute(
                "SELECT feature_name FROM feature_snapshots WHERE decision_id = ?",
                (did,),
            ).fetchall()
        }
        self.assertEqual(feats_rows, {"confidence_band", "abstain_reason", "n_tools"})


# ── Chunk-level evidence capture ────────────────────────────────────────────


class _FakeKS:
    """Minimal knowledge_store stub for chunk-id plumbing tests.

    ``query`` returns plain docs (legacy shape) and ``query_with_refs`` returns
    (docs, refs) with per-collection ids so we can assert that debate agents
    surface chunk-level ids through to the ledger.
    """

    def __init__(self) -> None:
        self._store = {
            "price_movements": [
                {"id": "pm-1", "doc": "AAPL up 2% today"},
                {"id": "pm-2", "doc": "Record Q4 volume"},
            ],
            "sp500_fundamentals_narratives": [
                {"id": "fn-1", "doc": "Strong iPhone cycle"},
            ],
            "debate_history": [{"id": "dh-1", "doc": "prior AAPL BUY"}],
            "youtube_insights": [{"id": "yt-1", "doc": "bullish channel sentiment"}],
            "stock_profiles": [{"id": "sp-1", "doc": "mega cap tech"}],
            "earnings_memory": [{"id": "em-1", "doc": "Q3 beat"}],
        }

    def query(self, collection, query_text, n_results=3, where=None):
        rows = self._store.get(collection, [])[:n_results]
        return [r["doc"] for r in rows]

    def query_with_refs(self, collection, query_text, n_results=3, where=None):
        rows = self._store.get(collection, [])[:n_results]
        docs = [r["doc"] for r in rows]
        refs = [
            {
                "chunk_id": r["id"],
                "collection": collection,
                "rank": i,
                "distance": 0.3,
                "ticker": "AAPL",
            }
            for i, r in enumerate(rows)
        ]
        return docs, refs

    def query_reflections(self, query_text, n_results=3, filters=None):
        return [], [], {"retrieved_docs_count": 0, "reflection_hits": 0}

    def format_context(self, docs):
        return "\n".join(d if isinstance(d, str) else str(d) for d in docs or [])


class TestDebateEvidenceIdsLandInLedger(_LedgerTestBase):
    def test_debate_evidence_captures_chunk_ids_per_agent(self) -> None:
        from backend import debate_agents as da
        from backend.schemas import AgentStance, DebateArgument

        ks = _FakeKS()

        class _LLM:
            async def generate_argument(self, role, ticker, live_data, context):
                return {"headline": f"{role} view", "key_points": ["p"], "confidence": 0.6,
                        "stance": "BULL" if role == "bull" else "NEUTRAL"}

            async def generate_moderator_verdict(self, ticker, args_dicts, context):
                return {"verdict": "BUY", "confidence": 0.8, "summary": "s"}

        llm = _LLM()
        macro_state = {"market_regime": "BULL_NORMAL", "credit_stress_index": 1.0}
        result = asyncio.run(
            da._run_full_debate_impl(
                "aapl", {"ticker": "AAPL"}, macro_state, ks, llm,
            )
        )
        self.assertEqual(result.verdict, "BUY")

        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="debate")
        self.assertEqual(len(decisions), 1)
        did = decisions[0].decision_id

        backend = dl.get_ledger()
        conn = backend._conn()  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT chunk_id, collection FROM decision_evidence "
            "WHERE decision_id = ?",
            (did,),
        ).fetchall()
        chunk_ids = {r["chunk_id"] for r in rows}
        collections = {r["collection"] for r in rows}
        # Moderator always queries debate_history
        self.assertIn("dh-1", chunk_ids)
        self.assertIn("debate_history", collections)
        # At least one per-agent retrieval landed (e.g. bull queries price_movements)
        self.assertIn("pm-1", chunk_ids)
        self.assertIn("price_movements", collections)


class TestChatEvidenceContractCarriesRagChunkRefs(unittest.TestCase):
    def test_rag_chunk_refs_surface_in_evidence_contract(self) -> None:
        from backend.chat_evidence_contract import build_evidence_contract

        meta = {
            "rag_nonempty": True,
            "coral_hub_nonempty": False,
            "rag_chunk_refs": [
                {"chunk_id": "x1", "collection": "price_movements", "rank": 0,
                 "distance": 0.2, "ticker": "AAPL"},
                {"chunk_id": "x2", "collection": "earnings_memory", "rank": 1,
                 "distance": 0.5, "ticker": "AAPL"},
                {"not": "a dict"},
            ],
        }
        ev = build_evidence_contract(
            tool_trace=[{"name": "get_stock_quote", "outcome": "success"}],
            quote_card_tickers=[],
            meta=meta,
        )
        self.assertEqual(ev["schema_version"], 2)
        refs = ev["rag_chunk_refs"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["chunk_id"], "x1")
        self.assertEqual(refs[0]["collection"], "price_movements")
        self.assertAlmostEqual(refs[0]["distance"], 0.2, places=4)
        self.assertEqual(refs[1]["ticker"], "AAPL")

    def test_rag_chunk_refs_default_empty(self) -> None:
        from backend.chat_evidence_contract import build_evidence_contract

        ev = build_evidence_contract(
            tool_trace=[], quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        self.assertEqual(ev["rag_chunk_refs"], [])


class TestChatRagContextPopulatesOutRefs(unittest.TestCase):
    """``chat_rag_context`` must populate ``out_refs`` with per-hit chunk ids."""

    def test_out_refs_populated_from_ranked_hits(self) -> None:
        from backend import chat_service

        class _KS:
            def query_with_metadata(self, collection, query_text, n_results=8, where=None):
                # Two hits per collection; ids include the collection so we can
                # verify the `collection` tag propagates through merge + rerank.
                return [
                    {
                        "id": f"{collection}-a",
                        "document": f"doc a from {collection}",
                        "metadata": {"ticker": "AAPL"},
                        "distance": 0.1,
                    },
                    {
                        "id": f"{collection}-b",
                        "document": f"doc b from {collection}",
                        "metadata": {"ticker": "AAPL"},
                        "distance": 0.3,
                    },
                ]

        async def _run():
            out: list = []
            block = await chat_service.chat_rag_context(
                _KS(), "Tell me about AAPL", out_refs=out,
            )
            return block, out

        block, refs = asyncio.run(_run())
        self.assertTrue(block)
        self.assertGreater(len(refs), 0)
        # Every ref carries chunk_id + collection + distance fields.
        for r in refs:
            self.assertTrue(r["chunk_id"])
            self.assertTrue(r["collection"])
            self.assertIn("rank", r)
            self.assertIn("distance", r)
            self.assertIn("-", r["chunk_id"])  # "<collection>-<a|b>"

        # And the collection tag matches the ``<col>-<suffix>`` id prefix.
        for r in refs:
            prefix = r["chunk_id"].split("-", 1)[0]
            self.assertEqual(prefix, r["collection"])


if __name__ == "__main__":
    unittest.main()
